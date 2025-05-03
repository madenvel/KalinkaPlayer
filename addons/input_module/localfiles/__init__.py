from addons.input_module.localfiles.localfiles import LocalFilesInputModule


def get_client(config):
    from addons.input_module.localfiles.db_manager import DbManager

    return DbManager(config)
