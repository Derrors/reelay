---
layout: home

title: 从 Telegram 消息到 115 媒体库
hero:
  name: Reelay
  text: 从消息流，<br>到自己的媒体库
  tagline: 自托管的 Telegram → 115 资源整理工具。识别、筛选、审批、追剧，把反复转存与改名的事交给后台，把决定权留给你。
  actions:
    - theme: brand
      text: 快速开始
      link: /quickstart
    - theme: alt
      text: 什么是 Reelay？
      link: /introduction
    - theme: alt
      text: GitHub
      link: https://github.com/Derrors/reelay

features:
  - title: 消息进来，先识别与筛选
    details: 用自己的 Telegram 账号监听来源；AI 或规则提取片名、季集与版本，再结合 TMDB 和文件信息核对。
    link: /user-guide#配置监听来源
    linkText: 配置你的来源
  - title: 是否保存，由你掌控
    details: 逐条确认、批量审批，或按规则自动放行。拿不准的留给你，识别错误可以修正、重新判断。
    link: /user-guide#选择适合你的处理方式
    linkText: 选择处理方式
  - title: 按季追剧，关注缺集
    details: 订阅某一季，符合条件的新集自动通过；启用追更插件后，记录已入库的集并提醒缺集。
    link: /user-guide#追剧订阅与不再接收
    linkText: 了解追剧订阅
  - title: 转存之后，还要核验
    details: 支持 115 分享与电驴云下载。先保存检查点，再执行转存；结果不明先核验，不盲目重复提交。
    link: /user-guide#转存或整理异常时怎么办
    linkText: 看懂任务状态
  - title: 分类、命名，一起整理
    details: 按媒体类型或二级分类入库，自定义命名模板；遇到重复版本，可人工确认、保留、并存或按偏好替换。
    link: /user-guide#保存目录与整理
    linkText: 设置整理方式
  - title: 网页配置，Telegram 日常操作
    details: 管理台用于配置和排查，控制 Bot 用于审批、追剧和通知；MCP 为外部 Agent 提供查询与管理工具。
    link: /agents
    linkText: 了解操作入口
---

<div class="home-guide">
  <h2>从你现在要做的事开始</h2>
  <p class="intro">第一次不必填完所有参数，也不必读完整本手册。先走通一条资源，再逐步开启自动化。</p>
  <div class="reading-paths">
    <a href="./quickstart.html"><strong>第一次部署</strong><span>准备账号、启动管理台、添加来源，直到第一条资源确认入库。每一步都有成功标志。</span><em>七步完成上手 →</em></a>
    <a href="./user-guide.html"><strong>已经开始使用</strong><span>按场景理解审批、订阅与整理设置，分清“批准”“排队”和“已入库”。</span><em>查看日常使用指南 →</em></a>
    <a href="./faq.html"><strong>某一步遇到问题</strong><span>从页面访问、收不到消息，到 AI 识别、115 风控和整理失败，按阶段查找。</span><em>查看 24 个常见问题 →</em></a>
  </div>
  <div class="home-note"><strong>先了解边界：</strong>Reelay 不提供资源库、播放器、网盘挂载或 STRM 生成；媒体服务器读取 115 文件需另行配置。文档跟随源码，标记为“未发布”的功能不一定在正式镜像中。<a href="./introduction.html#它不是什么">查看能力边界</a> · <a href="https://github.com/Derrors/reelay/releases">查看正式版本</a></div>
</div>
