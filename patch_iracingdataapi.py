#!/usr/bin/env python3
"""
Patch script to fix Python 3.8 compatibility issues in iracingdataapi 1.3.0+

This script modifies the installed iracingdataapi package to use Python 3.8
compatible type hints (List[] instead of list[], Dict[] instead of dict[]).

Run this after installing dependencies:
    pip install -r requirements.txt
    python patch_iracingdataapi.py
"""

import os
import sys
import re


def find_iracingdataapi_path():
    """Find the installed iracingdataapi package path."""
    try:
        import iracingdataapi
        package_path = os.path.dirname(iracingdataapi.__file__)
        return package_path
    except ImportError:
        print("ERROR: iracingdataapi is not installed. Run 'pip install -r requirements.txt' first.")
        sys.exit(1)


def patch_type_hints(file_path):
    """Patch a Python file to use Python 3.8 compatible type hints."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        original_content = content

        # Check if typing imports exist
        has_list_import = re.search(r'from typing import.*\bList\b', content)
        has_dict_import = re.search(r'from typing import.*\bDict\b', content)

        # Add missing imports if needed
        if not has_list_import or not has_dict_import:
            # Find the typing import line
            typing_import_match = re.search(r'^from typing import (.+)$', content, re.MULTILINE)
            if typing_import_match:
                imports = typing_import_match.group(1).split(',')
                imports = [imp.strip() for imp in imports]

                if not has_list_import and 'List' not in imports:
                    imports.append('List')
                if not has_dict_import and 'Dict' not in imports:
                    imports.append('Dict')

                new_import = 'from typing import ' + ', '.join(imports)
                content = content.replace(typing_import_match.group(0), new_import)
            else:
                # Add typing import at the top after docstring
                lines = content.split('\n')
                insert_pos = 0
                in_docstring = False

                for i, line in enumerate(lines):
                    if '"""' in line or "'''" in line:
                        if not in_docstring:
                            in_docstring = True
                        else:
                            insert_pos = i + 1
                            break
                    elif not in_docstring and line.strip() and not line.startswith('#'):
                        insert_pos = i
                        break

                imports_needed = []
                if not has_list_import:
                    imports_needed.append('List')
                if not has_dict_import:
                    imports_needed.append('Dict')

                if imports_needed:
                    lines.insert(insert_pos, f"from typing import {', '.join(imports_needed)}")
                    content = '\n'.join(lines)

        # Replace list[...] with List[...]
        content = re.sub(r'\blist\[', 'List[', content)

        # Replace dict[...] with Dict[...]
        content = re.sub(r'\bdict\[', 'Dict[', content)

        # Replace set[...] with Set[...]
        content = re.sub(r'\bset\[', 'Set[', content)

        # Replace tuple[...] with Tuple[...]
        content = re.sub(r'\btuple\[', 'Tuple[', content)

        if content != original_content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True

        return False

    except Exception as e:
        print(f"ERROR patching {file_path}: {e}")
        return False


def main():
    print("=" * 60)
    print("iRacingDataAPI Python 3.8 Compatibility Patcher")
    print("=" * 60)
    print()

    # Find the package
    package_path = find_iracingdataapi_path()
    print(f"Found iracingdataapi at: {package_path}")
    print()

    # Find all Python files in the package
    patched_files = []

    for root, dirs, files in os.walk(package_path):
        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, package_path)

                print(f"Checking {relative_path}...", end=' ')

                if patch_type_hints(file_path):
                    print("PATCHED ✓")
                    patched_files.append(relative_path)
                else:
                    print("OK (no changes needed)")

    print()
    print("=" * 60)

    if patched_files:
        print(f"Successfully patched {len(patched_files)} file(s):")
        for file in patched_files:
            print(f"  - {file}")
        print()
        print("The package is now compatible with Python 3.8!")
    else:
        print("No files needed patching. Package may already be compatible.")

    print("=" * 60)


if __name__ == "__main__":
    main()
