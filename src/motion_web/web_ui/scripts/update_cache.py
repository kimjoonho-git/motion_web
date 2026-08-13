#!/usr/bin/env python3
import os
import re
import sys
import time

def main():
    if len(sys.argv) < 2:
        print("Usage: update_cache.py <target_directory>")
        sys.exit(1)
        
    target_dir = sys.argv[1]
    if not os.path.isdir(target_dir):
        print(f"Error: Directory not found - {target_dir}")
        sys.exit(1)

    timestamp = time.strftime('%Y%m%d%H%M%S')
    # Matches ?v= followed by digits and dashes/alphanumerics
    pattern = re.compile(r'(\?v=)[0-9a-zA-Z_-]+')
    
    updated_files = 0
    for root, _, files in os.walk(target_dir):
        for f in files:
            if f.endswith(('.html', '.js', '.css')):
                path = os.path.join(root, f)
                with open(path, 'r', encoding='utf-8') as file:
                    content = file.read()
                
                new_content = pattern.sub(r'\g<1>' + timestamp, content)
                
                if content != new_content:
                    with open(path, 'w', encoding='utf-8') as file:
                        file.write(new_content)
                    updated_files += 1

    print(f"[Cache Buster] Updated {updated_files} files with version tag ?v={timestamp}")

if __name__ == '__main__':
    main()
