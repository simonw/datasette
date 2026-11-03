> **语言**：本文档为简体中文翻译，[点此查看英文原版 README.md](README.md)。

<img src="https://datasette.io/static/datasette-logo.svg" alt="Datasette">

[![PyPI](https://img.shields.io/pypi/v/datasette.svg)](https://pypi.org/project/datasette/)
[![Changelog](https://img.shields.io/github/v/release/simonw/datasette?label=changelog)](https://docs.datasette.io/en/latest/changelog.html)
[![Python 3.x](https://img.shields.io/pypi/pyversions/datasette.svg?logo=python&logoColor=white)](https://pypi.org/project/datasette/)
[![Tests](https://github.com/simonw/datasette/workflows/Test/badge.svg)](https://github.com/simonw/datasette/actions?query=workflow%3ATest)
[![Documentation Status](https://readthedocs.org/projects/datasette/badge/?version=latest)](https://docs.datasette.io/en/latest/?badge=latest)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/simonw/datasette/blob/main/LICENSE)
[![docker: datasette](https://img.shields.io/badge/docker-datasette-blue)](https://hub.docker.com/r/datasetteproject/datasette)
[![discord](https://img.shields.io/discord/823971286308356157?label=discord)](https://datasette.io/discord)

*一款用于探索与发布数据的开源多面工具*

Datasette 是一款用于探索与发布数据的工具。它帮助人们把任何形态、任何规模的数据发布为一个可交互、可浏览的网站以及配套的 API。

Datasette 面向数据记者、博物馆策展人、档案管理员、地方政府、科学家、研究者，以及其他任何拥有希望与世界分享的数据的人。

[探索一个演示](https://datasette.io/global-power-plants/global-power-plants)，观看[一段关于本项目的视频](https://simonwillison.net/2021/Feb/7/video/)，或[在 GitHub Codespaces 上试用](https://github.com/datasette/datasette-studio)。

* [datasette.io](https://datasette.io/) 是官方项目网站
* 最新的 [Datasette 动态（News）](https://datasette.io/news)
* 详尽的文档：https://docs.datasette.io/
* 示例：https://datasette.io/examples
* 当前 `main` 分支的在线演示：https://latest.datasette.io/
* 有疑问、想反馈，或想讨论这个项目？欢迎加入我们的 [Discord](https://datasette.io/discord)

想随时了解项目进展？订阅 [Datasette 通讯（newsletter）](https://datasette.substack.com/)，获取实用技巧、诀窍以及 Datasette 生态的最新消息。

## 安装

如果你使用的是 Mac，[Homebrew](https://brew.sh/) 是安装 Datasette 最简单的方式：

    brew install datasette

你也可以使用 `pip` 或 `pipx` 来安装：

    pip install datasette

Datasette 需要 Python 3.11 或更高版本。我们还提供了[详细的安装说明](https://docs.datasette.io/en/stable/installation.html)，涵盖 Docker 等其他安装方式。

## 基本用法

    datasette serve path/to/database.db

这会在 8001 端口启动一个 Web 服务器——访问 http://localhost:8001/ 即可进入 Web 界面。

`serve` 是默认子命令，你可以省略它。

在 OS X 上使用 Chrome？你可以像这样让 datasette 针对你的浏览器历史记录运行：

     datasette ~/Library/Application\ Support/Google/Chrome/Default/History --nolock

此时访问 http://localhost:8001/History/downloads 就会向你展示一个用于浏览你的下载数据的 Web 界面：

![Downloads table rendered by datasette](https://static.simonwillison.net/static/2017/datasette-downloads.png)

## metadata.json

如果你希望在生成的 datasette 网站中包含许可与来源信息，可以使用一个形如这样的 JSON 文件：

    {
        "title": "Five Thirty Eight",
        "license": "CC Attribution 4.0 License",
        "license_url": "http://creativecommons.org/licenses/by/4.0/",
        "source": "fivethirtyeight/data on GitHub",
        "source_url": "https://github.com/fivethirtyeight/data"
    }

将其保存为 `metadata.json`，然后像这样运行 Datasette：

    datasette serve fivethirtyeight.db -m metadata.json

许可与来源信息会显示在索引页和页脚中，也会被包含进 API 生成的 JSON 里。

## datasette publish

如果你已经配置好了 [Heroku](https://heroku.com/) 或 [Google Cloud Run](https://cloud.google.com/run/)，Datasette 可以用一条命令把一个或多个 SQLite 数据库部署到互联网上：

    datasette publish heroku database.db

或者：

    datasette publish cloudrun database.db

这会创建一个 docker 镜像，其中同时包含 datasette 应用和你指定的 SQLite 数据库文件。随后它会把该镜像部署到 Heroku 或 Cloud Run，并给你一个可访问最终网站与 API 的 URL。

更多细节请参阅文档中的[发布数据（Publishing data）](https://docs.datasette.io/en/stable/publish.html)。

## Datasette Lite

[Datasette Lite](https://lite.datasette.io/) 是使用 WebAssembly 打包的 Datasette，它完全在你的浏览器中运行，无需 Python Web 应用服务器。更多内容请阅读 [Datasette Lite 文档](https://github.com/simonw/datasette-lite/blob/main/README.md)。
