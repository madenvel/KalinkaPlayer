import yaml

config_path = "/opt/kalinka/kalinka_conf.yaml"


def set_config_path(path):
    global config_path
    config_path = path


with open("kalinka_conf.yaml", "r") as f:
    config = yaml.safe_load(f)
