#!/usr/bin/env python3
"""Custom mutmut runner that sets up Python path correctly."""
import sys
import os
import subprocess
import shutil
import tempfile

def main():
    project_root = os.path.dirname(os.path.abspath(__file__))
    
    # Remove old mutants directory
    mutants_dir = os.path.join(project_root, 'mutants')
    if os.path.exists(mutants_dir):
        shutil.rmtree(mutants_dir)
    
    os.chdir(project_root)
    env = os.environ.copy()
    env['PYTHONPATH'] = project_root + ':' + env.get('PYTHONPATH', '')
    
    # Create a custom runner script that sets up the Python path
    runner_script = f"""#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, '{project_root}')
os.chdir('{project_root}')
import pytest
sys.exit(pytest.main(['-x', '-q', '--import-mode=append', '-c', '/dev/null'] + sys.argv[1:]))
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(runner_script)
        runner_path = f.name
    
    try:
        os.chmod(runner_path, 0o755)
        cmd = [
            sys.executable, '-m', 'mutmut', 'run',
            '--runner', f'{sys.executable} {runner_path}'
        ]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        return result.returncode
    finally:
        os.unlink(runner_path)

if __name__ == '__main__':
    import sys
    import os
    import subprocess
    import shutil
    import tempfile
    sys.exit(main())
