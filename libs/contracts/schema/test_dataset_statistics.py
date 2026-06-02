import json
import sys, os
from os.path import dirname

root_dir = dirname(dirname(dirname(__file__)))
if root_dir not in sys.path:
    sys.path.append(root_dir)

from data.schema.dataset_statistics import load_latest_dataset_statistics

result = load_latest_dataset_statistics()
print(json.dumps(result, ensure_ascii=False, indent=4))
