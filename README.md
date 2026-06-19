# 夸克学习资料分享码管理系统

## 功能

- 资料目录展示（按分类树形浏览）
- 用户三级权限（单次购买 / 打包购买 / 永久订阅）
- 实时调用夸克网盘 API 生成分享码
- 管理后台（资料增删改、用户管理、分享记录）

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

编辑 `settings.yaml`，填入夸克网盘 Cookie：

```yaml
quark_cookie: "你的夸克Cookie"  # 浏览器F12 → Network → 复制Cookie
admin:
  username: admin
  password: admin123
jwt_secret: "随机字符串"
```

> **获取夸克Cookie**：打开 [夸克网盘网页版](https://pan.quark.cn)，F12 → Network 标签 → 找到任意请求 → 复制完整的 Cookie 值。

### 3. 启动

```bash
python main.py
```

浏览器打开 `http://localhost:8000`

### 4. 添加资料

1. 用管理员账号登录（默认 admin / admin123）
2. 进入管理后台 → 资料管理 → 添加
3. 填写：资料名称、分类标签、夸克网盘中该资料的**文件夹ID**

> **获取文件夹ID**：在夸克网盘网页版中打开目标文件夹，URL 中的 `/folder/xxx` 部分的 `xxx` 即为文件夹ID。

## 使用流程

```
用户打开网页 → 注册/登录 → 浏览资料目录 → 勾选需要的资料
→ 点击"获取分享码" → 系统校验权限 → 生成夸克分享链接+提取码
→ 用户复制去夸克下载
```

管理员在后台设置用户的购买级别和可访问的资料范围。

## 项目结构

```
quark-share/
├── main.py            # FastAPI 入口
├── settings.yaml      # 配置文件
├── models.py          # SQLite 数据模型
├── quark_client.py    # QuarkPan API 封装
├── admin.py           # 管理后台 API
├── user.py            # 用户端 API
├── requirements.txt
├── README.md
├── static/
│   └── index.html     # 前端单页
└── data/
    └── quark_share.db # 数据库（自动创建）
```
