import yaml

config_path = "kalinka_conf.yaml"


def set_config_path(path):
    global config_path
    config_path = path


with open(config_path, "r") as f:
    config = yaml.safe_load(f)
