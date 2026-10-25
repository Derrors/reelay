import { useCallback, useEffect, useState } from 'react';
import { useBlocker } from 'react-router-dom';
import { toast } from 'sonner';
import { configApi, type ISaveSectionsResp } from '@/api/config';
import { confirmDialog } from '@/lib/confirm';
import type { IConfigResponse, IExtension, ISecretStatus } from '@/lib/types';
import { errorMessage } from '@/lib/utils';
import { equal, getPath, obj, setPath, type Obj } from '@/lib/values';
import { URL_SECRETS, clearSecrets, describeConfigError, dirNamesFrom, normalizeUrl } from './shared';

/**
 * 设置页与插件页共用的配置草稿：读取配置、在本地改、只提交改动的配置块（带版本号，避免覆盖别处的修改）。
 * keys 是本页负责的顶层配置块；有未保存的修改时，离开页面（含站内跳转）要先确认。
 */
export function useConfigDraft(keys: string[]) {
  const [data, setData] = useState<IConfigResponse | null>(null);
  const [config, setConfig] = useState<Obj>({});
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [errors, setErrors] = useState<string[]>([]);
  /** 目录编号 → 显示名（网盘路径或刚在弹窗里选的目录）。 */
  const [dirNames, setDirNames] = useState<Record<string, string>>({ '0': '根目录' });
  /** 改了接口地址时填的新密钥（name → 值），保存配置后再写入：后端会先清除旧地址的密钥。 */
  const [pendingSecrets, setPendingSecrets] = useState<Record<string, string>>({});

  const apply = useCallback((r: IConfigResponse) => {
    setData(r);
    setConfig(r.config);
    setErrors([]);
    setLoadError('');
    setPendingSecrets({});
    setDirNames((prev) => ({ ...prev, ...dirNamesFrom(r.config, r.save_dir_paths ?? {}) }));
  }, []);
  useEffect(() => {
    let active = true;
    configApi.get()
      .then((r) => { if (active) apply(r); })
      .catch((e: unknown) => { if (active) setLoadError(errorMessage(e, '配置加载失败')); });
    return () => { active = false; };
  }, [apply]);
  const reload = async () => {
    setLoadError('');
    try { apply(await configApi.get()); } catch (e) { setLoadError(errorMessage(e, '配置加载失败')); }
  };

  const changed = (path: string) => data !== null && !equal(getPath(config, path), getPath(data.config, path));
  const dirtyKeys = data ? keys.filter((k) => !equal(config[k], data.config[k])) : [];
  const dirty = dirtyKeys.length > 0;
  useLeaveGuard(dirty);

  const update = (key: string, value: unknown) => setConfig((c) => ({ ...c, [key]: value }));
  const setField = (key: string, name: string, value: unknown) =>
    setConfig((c) => ({ ...c, [key]: { ...obj(c[key]), [name]: value } }));
  /** 撤销修改；paths 可以是顶层键，也可以是 plugins.<名> 这样的路径。 */
  const restore = (paths: string[]) => {
    if (!data) return;
    setConfig((c) => paths.reduce((acc, path) => setPath(acc, path, structuredClone(getPath(data.config, path))), c));
    setErrors([]);
  };
  const setSecrets = (secrets: ISecretStatus) => setData((d) => (d ? { ...d, secrets } : d));
  const setPendingSecret = (name: string, value: string) => setPendingSecrets((prev) => ({ ...prev, [name]: value }));

  /** 保存成功后：已保存的配置与版本号换成新的，后端因地址变更清除的密钥标为未设置。 */
  const commit = (r: ISaveSectionsResp) => {
    setData((d) => (d ? { ...d, config: r.config, revision: r.revision, secrets: clearSecrets(d.secrets, r.cleared_secrets ?? []) } : d));
  };

  /** 保存本页所有改动；successText 是成功提示。 */
  const save = async (successText: string) => {
    if (!data) return;
    if (!dirtyKeys.length) { toast.info('没有需要保存的修改'); return; }
    const moved = URL_SECRETS.filter((x) => normalizeUrl(getPath(config, x.path)) !== normalizeUrl(getPath(data.config, x.path)));
    const lost = moved.filter((x) => data.secrets[x.secret]?.set);
    if (lost.length && !(await confirmDialog({
      title: '改了地址，原来的密钥会被清除',
      description: '密钥属于原来的地址：保存后清除，需要为新地址重新填写。',
      details: lost.map((x) => ({
        label: data.secrets[x.secret]?.label ?? x.label,
        value: pendingSecrets[x.secret]?.trim() ? '清除，改用你刚填写的新密钥' : '清除，保存后请重新填写',
      })),
      confirmText: '保存并清除密钥',
      destructive: true,
    }))) return;
    setBusy(true);
    try {
      await configApi.validate(config);
      const r = await configApi.saveSections(Object.fromEntries(dirtyKeys.map((k) => [k, config[k]])), data.revision);
      commit(r);
      setConfig(r.config);
      setErrors([]);
      // 新地址的密钥：配置保存（旧密钥清除）之后再写入
      const fresh = Object.fromEntries(moved.map((x) => [x.secret, pendingSecrets[x.secret]?.trim() ?? '']).filter(([, v]) => v));
      setPendingSecrets({});
      if (Object.keys(fresh).length) {
        try {
          setSecrets(await configApi.saveSecrets(fresh));
        } catch (e) {
          toast.error(`设置已保存，但新密钥没有保存成功：${errorMessage(e, '保存失败')}`);
          return;
        }
      }
      toast.success(successText);
    } catch (e) {
      const msg = describeConfigError(errorMessage(e, '保存失败'));
      setErrors([msg]);
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  /** 保存 AI 起草、用户确认过的配置块（一句话生成规则）；页面没有未保存的修改时才可调用。 */
  const applySections = async (sections: Record<string, unknown>, revision: string) => {
    if (!data) return false;
    setBusy(true);
    try {
      const r = await configApi.saveSections(sections, revision);
      commit(r);
      setConfig(r.config);
      setErrors([]);
      return true;
    } catch (e) {
      toast.error(errorMessage(e, '保存失败'));
      return false;
    } finally {
      setBusy(false);
    }
  };

  /** 插件开关即时保存：只提交这一个开关（基于已保存的配置），页面上其他未保存的修改原样保留。 */
  const toggleExtension = async (x: IExtension, enabled: boolean) => {
    if (!data || busy) return;
    const path = `plugins.${x.name}.enabled`;
    const before = getPath(config, path);
    const sections = { plugins: obj(setPath(data.config, path, enabled).plugins) };
    setConfig((c) => setPath(c, path, enabled));
    setBusy(true);
    try {
      await configApi.validate({ ...data.config, ...sections });
      const r = await configApi.saveSections(sections, data.revision);
      commit(r);
      setConfig((c) => setPath(c, path, getPath(r.config, path)));
      toast.success(`「${x.label}」已${enabled ? '开启' : '关闭'}并立即生效`);
    } catch (e) {
      setConfig((c) => setPath(c, path, before));
      toast.error(errorMessage(e, '设置保存失败'));
    } finally {
      setBusy(false);
    }
  };

  return {
    data, config, setConfig, update, setField, restore, changed, dirtyKeys, dirty, busy, loadError, errors,
    reload, save, applySections, toggleExtension, setSecrets,
    dirNames, rememberDir: (id: string, name: string) => setDirNames((prev) => ({ ...prev, [id]: name })),
    pendingSecrets, setPendingSecret,
  };
}

export type ConfigDraft = ReturnType<typeof useConfigDraft>;

/** 有未保存的修改时：关闭标签页由浏览器提示，站内跳转用页面内的确认弹窗。 */
function useLeaveGuard(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  // 只拦换页面：同一页面内切页签只改查询参数
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    void confirmDialog({
      title: '放弃未保存的修改？',
      description: '这个页面还有修改没有保存，离开后会丢失。',
      confirmText: '放弃并离开',
      cancelText: '留在这里',
      destructive: true,
    }).then((leave) => (leave ? blocker.proceed() : blocker.reset()));
  }, [blocker]);
}
