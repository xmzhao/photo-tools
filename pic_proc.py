import os, sys
import re
from datetime import datetime

if len(sys.argv) != 2:
    print(sys.argv[0], 'dir_path')
    sys.exit(-1)

dir_path = sys.argv[1] 

for folder in os.listdir(dir_path):
    if os.path.isdir(os.path.join(dir_path, folder)):
        date_pattern = r'(\w+ \d+, \d{4})'
        date_match = re.search(date_pattern, folder)
        if date_match:
            date_str = date_match.group(1)
            date_obj = datetime.strptime(date_str, '%B %d, %Y')
            new_name = date_obj.strftime('%Y%m%d') + '.' + folder
            os.rename(os.path.join(dir_path, folder), os.path.join(dir_path, new_name))
        else:
            continue