#!/usr/bin/env python3
"""Quick improvement script for simple files."""

import re
from pathlib import Path
from typing import Any

def add_basic_improvements(file_path: Path) -> bool:
    """Add basic type hints and docstrings to a file.
    
    Returns:
        True if successful, False otherwise.
    """
    try:
        content = file_path.read_text()
        
        # Skip if already improved
        if "-> None:" in content and '"""' in content:
            return True
        
        # Add basic imports if missing
        if "from typing import" not in content:
            # Find first import
            import_match = re.search(r'^(import .+)$', content, re.MULTILINE)
            if import_match:
                insert_pos = import_match.start()
                content = (
                    content[:insert_pos] + 
                    "from typing import Any\n\n" +
                    content[insert_pos:]
                )
        
        # Add return type hints to __init__ methods
        content = re.sub(
            r'(def __init__\(self[^)]*\):',
            r'\1 -> None:',
            content
        )
        
        # Add basic docstring to classes
        content = re.sub(
            r'(class (\w+).*?:\n    """[^"]*""")',
            r'class \2:\n    """\2 container.\n    \n    This class handles \2 data.',
            content
        )
        
        file_path.write_text(content)
        return True
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return False

if __name__ == "__main__":
    import sys
    files = sys.argv[1:]
    for f in files:
        path = Path(f)
        if add_basic_improvements(path):
            print(f"✓ {path.name}")
        else:
            print(f"✗ {path.name}")
