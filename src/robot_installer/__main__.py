def main():
    import argparse

    parser = argparse.ArgumentParser(description="机器人 Web 部署工具")
    parser.add_argument("--smoke-test", action="store_true", help="验证打包后 GUI 和运行时并退出")
    parser.add_argument("--snapshot", help="运行时验证时保存界面截图")
    args = parser.parse_args()
    if args.smoke_test:
        import json
        import platform
        from PySide6.QtCore import qVersion
        from . import __version__
        from .ui import MainWindow, create_application
        from .config import RobotConfig
        from .terminal import TerminalWindow

        app = create_application()
        window = MainWindow(auto_connect_nas=False)
        window.show()
        terminal = TerminalWindow(RobotConfig(), autoconnect=False)
        terminal.show()
        for _ in range(3):
            terminal.add_split_button.click()
        app.processEvents()
        if args.snapshot:
            window.grab().save(args.snapshot)
        print(
            json.dumps(
                {
                    "version": __version__,
                    "qt": qVersion(),
                    "libc": platform.libc_ver(),
                    "gui": "ok",
                    "generation": window.generation.currentText(),
                    "terminal_panes": terminal.count,
                },
                ensure_ascii=False,
            )
        )
        window.close()
        terminal.close()
        return
    from .ui import launch

    raise SystemExit(launch())


if __name__ == "__main__":
    main()
