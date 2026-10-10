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
    
    # Run mutmut with custom runner that sets up the Python path
    os.chdir(project_root)
    
    # Create a custom pytest config that will be copied to mutants dir
    pytest_config = """[pytest]
testpaths = tests
pythonpath = ..
ignore = 
    tests/test_server_discovery.py
    tests/test_server_login_account.py
    tests/test_git_remotes.py
    tests/test_login_publish_push.py
    tests/test_git_baselines.py
    tests/test_git_baselines_edge_cases.py
    tests/test_git_remotes_edge_cases.py
    tests/test_git_history_conflicts_and_isolation.py
    tests/test_git_history_safety.py
    tests/test_apply_skeleton_project_template.py
    tests/test_git_remote_security.py
"""
    
    # Run mutmut with custom runner
    env = os.environ.copy()
    env['PYTHONPATH'] = os.path.dirname(os.path.abspath(__file__)) + ':' + env.get('PYTHONPATH', '')
    
    # Create a custom runner script
    runner_script = f"""#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, '{os.path.dirname(os.path.abspath(__file__))}')
os.chdir('{os.path.dirname(os.path.abspath(__file__))}')
import pytest
sys.exit(pytest.main(['-x', '-q'] + sys.argv[1:]))
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
    
    project_root = os.path.dirname(os.path.abspath(__file__))
    mutants_dir = os.path.join(project_root, 'mutants')
    if os.path.exists(mutants_dir):
        shutil.rmtree(mutants_dir)
    
    os.chdir(project_root)
    env = os.environ.copy()
    env['PYTHONPATH'] = project_root + ':' + env.get('PYTHONPATH', '')
    
    # Create a custom runner script
    runner_script = f"""#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, '{project_root}')
os.chdir('{project_root}')
import pytest
sys.exit(pytest.main(['-x', '-q'] + sys.argv[1:]))
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
        sys.exit(result.returncode)
    finally:
        os.unlink(runner_path)

if __name__ == '__main__':
    import sys
    import os
    import subprocess
    import shutil
    import tempfile
    sys.exit(main())
