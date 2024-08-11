import importlib.util
import sys
import os


def import_module_by_path(module_path):
    # Convert module path to file path
    module_name = module_path.replace(".", "/")
    file_path = os.path.join(module_name + ".py")

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"The file {file_path} does not exist")

    spec = importlib.util.spec_from_file_location(module_path, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_path] = module
    spec.loader.exec_module(module)

    return module
