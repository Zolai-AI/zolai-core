"""
Zolai Rules Reference — comprehensive grammar and orthography rules.

Single source of truth for Zolai language rules.
Consolidates ZVS 2018, SOV, tense, negation, particles, questions, ergative.
"""

class ZolaiRules:
    """Comprehensive Zolai language rules reference."""

    # ZVS 2018 Forbidden Forms
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

    # Tense markers
    TENSE_MARKERS = {
        '-sak': 'past completed',
        '-nak': 'infinitive/purpose',
        '-ah': 'present progressive',
        '-hen': 'past progressive',
        'a-': 'present perfect',
        'ka-': 'past perfect',
        'ta-': 'future',
        'ding': 'future marker',
    }

    # Negation
    NEGATION = {
        'lo': 'general negation',
        'kei': 'negative (before verb)',
        'si': 'negative imperative',
    }

    # Particles
    PARTICLES = {
        'hi': 'declarative (ends sentence)',
        'hiam': 'question marker',
        'pen': 'focus marker',
        'zong': 'also/search',
        'le': 'conjunction/if',
        'leh': 'and/if',
    }

    # Pronouns
    PRONOUNS = {
        'ka': '1st person singular (I/my)',
        'na': '2nd person (you/your)',
        'a': '3rd person (he/she/it/his/her)',
        'i': '1st person inclusive (we)',
        'uh': '3rd person plural marker',
    }

    # Word order
    WORD_ORDER = 'SOV'  # Subject-Object-Verb

    # Ergative marker
    ERGATIVE = 'in'  # Marks transitive verb subjects

    @classmethod
    def get_token_efficient_summary(cls) -> str:
        """Get token-efficient rules summary (<200 tokens)."""
        return (
            "Zolai Rules (ZVS 2018):\n"
            "1. Word order: SOV\n"
            "2. Ergative 'in' for transitive subjects\n"
            f"3. Forbidden: {', '.join(cls.FORBIDDEN_FORMS.keys())}\n"
            "4. Tense: -sak(past), -nak(purpose), -ah(present), -hen(prog), ding(future)\n"
            "5. Negation: lo/general, kei/before verb, si/imperative\n"
            "6. Questions: hiam at end\n"
            "7. Possessive: ka(my), na(your), a(his/her)\n"
        )

    @classmethod
    def check_forbidden(cls, text: str) -> list[tuple[str, str, str]]:
        """Check text for forbidden forms, return violations."""
        import re
        violations = []
        for forbidden, suggested in cls.FORBIDDEN_FORMS.items():
            pattern = r'\b' + re.escape(forbidden) + r'\b'
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            for match in matches:
                start = max(0, match.start() - 10)
                end = min(len(text), match.end() + 10)
                context = text[start:end]
                violations.append((forbidden, suggested, context))
        return violations


def get_rules_reference() -> ZolaiRules:
    """Get the Zolai rules reference."""
    return ZolaiRules()
