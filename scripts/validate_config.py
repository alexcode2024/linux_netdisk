from backend.app.config import load_settings


if __name__ == "__main__":
    settings = load_settings()
    print(f"配置有效：{len(settings.storage.allowed_roots)} 个授权目录")
