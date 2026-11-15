from PySide6.QtCore import QStorageInfo

def get_drive_space_info():
    """PC上のすべてのマウント済みドライブの容量情報を取得する"""
    drives_info = []
    for storage in QStorageInfo.mountedVolumes():
        if storage.isValid() and storage.isReady():
            # ルートパス（例: "C:/"）
            root_path = storage.rootPath()
            total_gb = storage.bytesTotal() / (1024 ** 3)
            free_gb = storage.bytesFree() / (1024 ** 3)
            used_gb = total_gb - free_gb
            usage_percent = (used_gb / total_gb * 100) if total_gb > 0 else 0
            
            drives_info.append({
                "path": root_path,
                "name": storage.displayName(),
                "total": total_gb,
                "used": used_gb,
                "free": free_gb,
                "percent": usage_percent
            })
    return drives_info