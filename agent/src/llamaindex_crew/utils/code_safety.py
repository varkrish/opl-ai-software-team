"""
Basic code safety checks and validation
Preserved from original implementation
"""
import re
import logging
from typing import List, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class CodeSafetyChecker:
    """Basic code safety checker for common dangerous patterns"""
    
    def __init__(self):
        # Forbidden patterns by language
        self.forbidden_patterns = {
            'python': [
                (r'import\s+(os|subprocess|sys|socket)', 'Dangerous system imports'),
                (r'os\.system\s*\(', 'Direct system command execution'),
                (r'subprocess\.(call|run|Popen)', 'Subprocess execution'),
                (r'eval\s*\(', 'Code evaluation'),
                (r'exec\s*\(', 'Code execution'),
                (r'__import__\s*\(', 'Dynamic import'),
                (r'open\s*\([^)]*[\'"]w[\'"]', 'File write operations'),
            ],
            'javascript': [
                (r'eval\s*\(', 'Code evaluation'),
                (r'Function\s*\(', 'Dynamic function creation'),
                (r'child_process', 'Child process execution'),
                (r'fs\.(rmSync|unlinkSync)', 'File deletion'),
                (r'process\.exit', 'Process termination'),
            ],
            'bash': [
                (r'rm\s+-rf', 'Recursive deletion'),
                (r'curl\s+.*\|.*sh', 'Remote script execution'),
                (r'wget\s+.*\|.*sh', 'Remote script execution'),
            ]
        }
        
        # Maximum file size (1MB)
        self.max_file_size = 1024 * 1024
    
    def check_code(self, code: str, language: str = 'python') -> Dict[str, any]:
        """
        Check code for dangerous patterns
        
        Returns:
            Dict with 'safe' (bool), 'issues' (list), 'blocked' (bool)
        """
        issues = []
        blocked = False
        
        # Check file size
        code_size = len(code.encode('utf-8'))
        if code_size > self.max_file_size:
            issues.append(f"File too large: {code_size} bytes (max: {self.max_file_size})")
            blocked = True
        
        # Check for forbidden patterns
        patterns = self.forbidden_patterns.get(language, [])
        for pattern, description in patterns:
            matches = re.finditer(pattern, code, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                line_num = code[:match.start()].count('\n') + 1
                issues.append(f"Line {line_num}: {description} - '{match.group(0)}'")
                # Some patterns are critical and should block
                if 'eval' in description or 'exec' in description or 'system' in description:
                    blocked = True
        
        return {
            'safe': len(issues) == 0,
            'issues': issues,
            'blocked': blocked,
            'language': language
        }
    
    def validate_file_path(self, file_path: str) -> Dict[str, any]:
        """Validate file path for safety"""
        issues = []
        blocked = False
        
        # Check for path traversal
        if '..' in file_path:
            issues.append("Path traversal detected (..)")
            blocked = True
        
        # Check for absolute paths outside workspace
        if file_path.startswith('/') and not file_path.startswith('/tmp'):
            issues.append("Absolute path outside workspace")
            blocked = True
        
        # Check for dangerous file extensions
        dangerous_extensions = ['.exe', '.sh', '.bat', '.cmd', '.ps1']
        if any(file_path.endswith(ext) for ext in dangerous_extensions):
            issues.append(f"Dangerous file extension: {file_path}")
            # Don't block, just warn
        
        return {
            'safe': len(issues) == 0,
            'issues': issues,
            'blocked': blocked
        }
    
    def check_file_write(self, file_path: str, content: str, language: str = 'python') -> Dict[str, any]:
        """Comprehensive check before writing a file"""
        path_check = self.validate_file_path(file_path)
        code_check = self.check_code(content, language)
        
        all_issues = path_check['issues'] + code_check['issues']
        blocked = path_check['blocked'] or code_check['blocked']
        
        return {
            'safe': len(all_issues) == 0,
            'issues': all_issues,
            'blocked': blocked,
            'path_check': path_check,
            'code_check': code_check
        }


def check_code_safety(code: str, language: str = 'python') -> Dict[str, any]:
    """Convenience function for code safety checking"""
    checker = CodeSafetyChecker()
    return checker.check_code(code, language)


def validate_file_write(file_path: str, content: str, language: str = 'python') -> Dict[str, any]:
    """Convenience function for file write validation"""
    checker = CodeSafetyChecker()
    return checker.check_file_write(file_path, content, language)
