# 上传 GitHub 与持续更新

## 项目目录

本机项目位于 `X:\hx_work\watermark_cleaner_v1`。
只把这个项目目录加入仓库，不要把整个 `X:\hx_work` 加进去。

代码仓库保存：`cleaner.py`、`smoke_test.py`、依赖清单、打包配置及文档。
`.gitignore` 已排除依赖目录、打包产物、测试报告和图片视频。
发布给使用者的 exe 作为 GitHub Release 附件上传。

## 第一次上传（GitHub Desktop）

1. 安装 GitHub Desktop：https://desktop.github.com/ ，登录你的 GitHub 账号。
2. 选择 File → Add Local Repository，选择此项目目录。
3. 当前目录还没有 Git 仓库；如果提示不是 Git 仓库，点击
   “create a repository here”。确认最终目录仍是上述项目目录：
   名称为 `watermark_cleaner_v1`，父目录为 `X:\hx_work`。
   不要另外创建嵌套文件夹，也不要覆盖已有 `.gitignore`。
4. 若 Changes 中还有未提交文件，填写说明，例如“首次提交：图片与视频去水印工具”，
   点击 Commit to main（若默认分支叫 master，则按钮名称相应变化）。
5. 点击 Publish repository，GitHub 仓库名称可设为 `watermark-cleaner`。
   “Keep this code private”勾选为私有，取消为公开。
   确认后发布。

提交前查看文件列表：不应出现 `vendor`、`build`、`dist*`、exe 或个人素材。
GitHub Desktop 创建仓库时可能自动完成首次提交；此时无需重复提交。
尚未添加开源许可证；如需允许他人复用代码，后续再选择适合的许可证。

## 发布可直接运行的程序

1. 打开 GitHub 仓库网页，进入 Releases → Draft a new release。
2. 创建版本标签，例如 `v0.1.0`，目标选择包含本次代码的分支。
3. 填写标题和更新说明。
4. 上传当前本机的 `dist_v3\WatermarkCleaner.exe` 作为附件。
5. 检查后点击 Publish release；尚未准备好可先 Save draft。

将 exe 放在 Release，源码放在仓库。无需把数百 MB 的依赖文件上传到仓库。

## 以后每次更新

1. 在 GitHub Desktop 中 Fetch origin；如有远端更新，先 Pull。
2. 在这个项目目录继续修改程序，更新 `CHANGELOG.md`。
3. 验证功能后，在 GitHub Desktop 的 Changes 中检查改动。
4. 填写此次改动说明 → Commit → Push origin。
5. 需要发布新程序时，重新打包并检查程序，然后新建一个 Release，
   使用新的版本号（例如 `v0.1.1`），上传对应的 exe。

每次 Release 的代码和 exe 应对应同一次提交。不要直接替换旧版标签的含义。
本项目暂未配置自动打包或客户端自动更新；更新源码不会自动生成或分发新 exe。

## 在其他电脑开发、验证和打包

需要 Windows、Python 3.10 或更新版本（建议 Python 3.12，含 Tkinter）。
在项目目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe smoke_test.py
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm WatermarkCleaner.spec
```

生成的程序位于 `dist\WatermarkCleaner.exe`。
打包配置已使用项目相对位置，不依赖原来的 X 盘路径或本机 `vendor` 目录。
原机的 `dist_v3` 是已有发布文件；后续上述标准打包命令默认输出到 `dist`。

可运行打包程序内置检查（在 PowerShell 中使用绝对报告路径）：

```powershell
$releaseReport = Join-Path $PWD 'packaged_release_test.txt'
Start-Process -FilePath '.\dist\WatermarkCleaner.exe' -ArgumentList '--self-test', ('"' + $releaseReport + '"') -Wait
Get-Content -LiteralPath $releaseReport
```

还应手动打开程序，用实际图片和视频检查一次，再上传 Release。

官方说明：
- https://docs.github.com/en/desktop/adding-and-cloning-repositories/adding-an-existing-project-to-github-using-github-desktop
- https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository
