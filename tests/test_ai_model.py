"""Offline OA inference contracts. All report content and responses are synthetic."""
import unittest

import httpx

from common.sanity_common import ReviewError, canonical_json
from common.sanity_integration_examples import clustering_example
from oa.sanity_ai_model import OAAdvisor






def environment(**extra):
    return {"SANITY_OA_AI_ENABLED": "true", "SANITY_OA_AI_URL": "https://model.invalid/v1/chat/completions",
            "SANITY_OA_AI_TOKEN": "synthetic-only-credential", "SANITY_OA_AI_MODEL": "synthetic-model-v1", **extra}


def chat(reply, **extra):
    return {"model": "synthetic-model-v1", "choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": canonical_json(reply).decode("utf-8")}}], **extra}


class OAModelTests(unittest.TestCase):
    def setUp(self):
        self.packet = clustering_example(with_history=True)

    def adapter(self, handler, **extra):
        return OAAdvisor(environment(**extra), transport=httpx.MockTransport(handler))


    def invoke(self, advisor):
        packet = self.packet
        return advisor.cluster(packet["context"], packet["cases"], packet["identity_fields"])


    def test_duplicate_nonfinite_invalid_utf8_and_nonobject_json_are_rejected(self):
        raw_envelopes = [b'{"choices": [], "choices": []}', b'{"usage":NaN}', b'{"usage":1e999}',
                         b'\xff', '{}'.encode('utf-16'), b'[]']
        for raw in raw_envelopes:
            with self.subTest(raw=raw), self.assertRaises(ReviewError) as error:
                self.invoke(self.adapter(lambda _: httpx.Response(200, content=raw)))
            self.assertEqual("INVALID_AI_RESPONSE", error.exception.code)
        for content in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '[]', '"\\ud800"'):
            envelope = chat(self.packet["reply"])
            envelope["choices"][0]["message"]["content"] = content
            with self.subTest(content=content), self.assertRaises(ReviewError) as error:
                self.invoke(self.adapter(lambda _: httpx.Response(200, json=envelope)))
            self.assertEqual("INVALID_AI_RESPONSE", error.exception.code)


if __name__ == "__main__":
    unittest.main()
