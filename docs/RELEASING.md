# Docker 发布流程

面向贡献者和项目维护者。公开使用者只需要发布附件中的 Compose 和 Docker，见部署说明。

## 发布门禁

发布前完成质量检查、公开 Compose 空数据卷初始化/重启、目标 NAS 真实业务验证及数据升级验收。检查将公开的文件、Git 历史、Docker 镜像配置与层、发布附件。已有私有部署记录不随仓库历史或镜像公开。

`release` 工作流仅允许从公开仓库的 `main` 手动运行，输入版本须与 `pyproject.toml`、Compose 和 CHANGELOG 一致。目前镜像平台为 `linux/amd64`；其他架构未验收，不能标为已支持。

## 发布产物

工作流复用完整 CI，下载该次验证实际生成的镜像和校验文件，校验后加载并推送 GHCR 的固定版本及源提交标签。发布包只允许以下内容：

- `compose.yaml`、`DOCKER.md`：独立部署示例和使用说明。
- `image-linux-amd64.tar.gz`：已测试镜像的离线 Docker 包，导入后标签与 Compose 一致。
- `build-sbom.cdx.json`：Python 依赖的 CycloneDX 清单，不声称覆盖系统软件包。
- `release.json`：版本、源提交、镜像 ID、注册表 digest 和平台。
- `release-notes.md`、`SHA256SUMS`：当前版本说明与全部附件校验值。

归档检查会验证镜像配置 ID、平台、运行用户、秘密配置和每一层的内容哈希。未知附件（包括 `.env`）直接拒绝。新工作流使用无注册表登录的环境拉取镜像，确认 ID 和 digest 一致后才创建 GitHub Release。已存在的版本不覆盖发布。

## GHCR 首次可见性

个人账号第一次推送的包默认私有，与仓库是否公开分开管理。须由包管理员在 Package settings 将它设为公开，才能通过匿名拉取门禁；这一步不能靠仓库公开状态推断。参见 [GitHub 官方包权限说明](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)。

如果 `image` 作业已成功、`publish` 因镜像仍私有失败，先完成包可见性设置，再重跑失败作业。不要把容器或发布成功的意图当成匿名用户已能安装的证据。

发布完成后以无登录环境验证镜像与附件下载，再在空目录按公开文档安装，记录真实 Telegram 登录、搜索、下载、入库、追更、重启及升级结果；验证码和两步密码仅由账号所有者在页面填写。
