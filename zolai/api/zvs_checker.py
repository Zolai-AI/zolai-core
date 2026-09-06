"""
ZVS 2018 Compliance Checker for AI Responses.

Blocks forbidden forms and suggests corrections.
"""
import re
from typing import Optional

# Forbidden forms mapping (ZVS 2018)
FORBIDDEN_FORMS = {
    'pathian': 'pasian',
    'ram': 'gam',
    'fapa': 'tapa',
    'bawipa': 'topa',
    'siangpahrang': 'kumpipa',
    'cu': 'tua',
    'cun': 'tua',
    'suah': 'chuak',
    'zalenna': 'suahtakna',
    'nunnak': 'nuntakna',
}

# Historical exceptions (allowed in quotes)
HISTORICAL_EXCEPTIONS = [
    'pathian', 'bawipa', 'siangpahrang', 'fapa',
    'zalenna', 'cun', 'cu'
]

class ZVSComplianceChecker:
    """Check AI responses for ZVS 2018 compliance."""

    def __init__(self, use_default_exceptions: bool = True):
        self.forbidden = FORBIDDEN_FORMS.copy()
        self.exceptions = set(HISTORICAL_EXCEPTIONS if use_default_exceptions else [])

    def check_response(self, text: str) -> dict:
        """
        Check if response contains forbidden forms.

        Returns:
            dict with keys:
                - is_compliant: bool
                - violations: list of (forbidden, suggested, context)
                - corrected_text: str with fixes applied
        """
        violations = []
        corrected = text

        for forbidden, suggested in self.forbidden.items():
            # Skip if in exceptions
            if forbidden in self.exceptions:
                continue

            # Find word boundary matches
            pattern = r'\b' + re.escape(forbidden) + r'\b'
            matches = list(re.finditer(pattern, text, re.IGNORECASE))

            for match in matches:
                start = max(0, match.start() - 20)
                end = min(len(text), match.end() + 20)
                context = text[start:end]
                violations.append((forbidden, suggested, context))

                # Replace in corrected text
                corrected = re.sub(pattern, suggested, corrected, flags=re.IGNORECASE)

        return {
            'is_compliant': len(violations) == 0,
            'violations': violations,
            'corrected_text': corrected
        }

    def suggest_correction(self, word: str) -> Optional[str]:
        """Get correction for a single word."""
        return self.forbidden.get(word.lower())

# Singleton instance
_checker_instance: Optional[ZVSComplianceChecker] = None

def get_zvs_checker() -> ZVSComplianceChecker:
    global _checker_instance
    if _checker_instance is None:
        _checker_instance = ZVSComplianceChecker()
    return _checker_instance

def check_zvs_compliance(text: str) -> dict:
    """Main entry point: check text for ZVS compliance."""
    return get_zvs_checker().check_response(text)
