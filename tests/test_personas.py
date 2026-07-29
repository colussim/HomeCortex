import unittest

from services.config_loader import PersonasConfig


class KnownPeopleTests(unittest.TestCase):
    def setUp(self):
        self.personas = PersonasConfig(
            {
                "family": {
                    "names": ["colussi", "véronique", "emmanuel", "adrien"],
                    "personal_questions": ["qui est", "qui sont", "c'est qui"],
                    "fallback_response": (
                        "Cette personne est un membre de la famille."
                    ),
                    "known_people": [
                        {
                            "aliases": ["Emmanuel Colussi", "Emmanuel"],
                            "response": (
                                "Emmanuel Colussi est mon créateur et propriétaire. "
                                "C'est lui qui m'a conçue et programmée."
                            ),
                        },
                        {
                            "aliases": [
                                "Véronique Colussi",
                                "Veronique Colussi",
                                "Véronique",
                            ],
                            "response": (
                                "Véronique Colussi est un membre de la famille."
                            ),
                        }
                    ],
                }
            }
        )

    def test_exact_known_person_response(self):
        self.assertEqual(
            self.personas.known_person_reply("Qui est Véronique COLUSSI ?"),
            "Véronique Colussi est un membre de la famille.",
        )

    def test_alias_without_accents(self):
        self.assertEqual(
            self.personas.known_person_reply("C'est qui Veronique Colussi ?"),
            "Véronique Colussi est un membre de la famille.",
        )

    def test_creator_response(self):
        self.assertEqual(
            self.personas.known_person_reply("Qui est Emmanuel Colussi ?"),
            (
                "Emmanuel Colussi est mon créateur et propriétaire. "
                "C'est lui qui m'a conçue et programmée."
            ),
        )

    def test_known_family_fallback_never_uses_web(self):
        self.assertEqual(
            self.personas.known_person_reply("Qui est Adrien ?"),
            "Cette personne est un membre de la famille.",
        )

    def test_non_question_does_not_trigger(self):
        self.assertEqual(
            self.personas.known_person_reply("Véronique arrive à la maison"),
            "",
        )

    def test_unknown_person_does_not_trigger(self):
        self.assertEqual(
            self.personas.known_person_reply("Qui est Ada Lovelace ?"),
            "",
        )

    def test_prompt_exact_rule_has_priority(self):
        prompt = """
Quand on te demande qui est Véronique Colussi :
→ Tu réponds exactement : "Réponse définie dans le prompt."
"""
        self.assertEqual(
            self.personas.prompt_person_reply(
                "Qui est Véronique Colussi ?", prompt
            ),
            "Réponse définie dans le prompt.",
        )

    def test_prompt_selects_longest_combined_name(self):
        prompt = """
Quand on te demande qui est Marc :
→ Tu réponds exactement : "Marc seul."
Quand on te demande qui sont Hélène et Marc Golay :
→ Tu réponds exactement : "Hélène et Marc ensemble."
"""
        self.assertEqual(
            self.personas.prompt_person_reply(
                "Qui sont Hélène et Marc Golay ?", prompt
            ),
            "Hélène et Marc ensemble.",
        )


if __name__ == "__main__":
    unittest.main()
