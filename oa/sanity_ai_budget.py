"""Explicit, offline token budgets for multi-call OA classification.

Never downloads weights or truncates input. The operator explicitly chooses
an offline tokenizer or conservative UTF-8 byte counting and reserves model
chat/reasoning overhead. Byte counts are not exact model token measurements.
Disabled settings leave existing single-call receipts unchanged.
"""
from dataclasses import dataclass, asdict, field
from functools import lru_cache
import hashlib
from pathlib import Path
import re

from common.sanity_common import ReviewError, canonical_json
from common.sanity_submission import check

BATCH_PROMPT_VERSION = 'oa-batch-clustering-v1'


@dataclass(frozen=True)
class BatchSettings:
    enabled: bool = False
    tokenizer_mode: str = 'utf8_bytes'
    tokenizer_file: str = field(default='', repr=False)
    tokenizer_sha256: str = ''
    context_tokens: int = 0
    input_tokens: int = 16000
    output_tokens: int = 4096
    overhead_tokens: int = 1024
    output_parameter: str = ''
    max_records: int = 200
    merge_groups: int = 20
    max_calls: int = 20000
    max_rounds: int = 8
    deadline_seconds: int = 86400
    max_concurrency: int = 1

    def __post_init__(self):
        check(type(self.enabled) is bool, 'AI_BATCH_CONFIG')
        for name, low, high in (('context_tokens',0,10000000),('input_tokens',1,1000000),
                ('output_tokens',1,1000000),('overhead_tokens',1,1000000),('max_records',1,1000),
                ('merge_groups',2,100),('max_calls',1,100000),('max_rounds',1,32),
                ('deadline_seconds',60,604800),('max_concurrency',1,8)):
            value = getattr(self,name)
            check(type(value) is int and low <= value <= high, 'AI_BATCH_CONFIG')
        if self.enabled:
            check(self.tokenizer_mode in ('utf8_bytes','tokenizer_json')
                  and self.output_parameter in ('max_tokens','max_completion_tokens','max_output_tokens')
                  and self.input_limit > 0, 'AI_BATCH_CONFIG', 'Configure verified model and tokenizer limits')
            if self.tokenizer_mode == 'tokenizer_json':
                check(isinstance(self.tokenizer_file,str) and Path(self.tokenizer_file).is_absolute()
                      and isinstance(self.tokenizer_sha256,str) and re.fullmatch('[0-9a-f]{64}',self.tokenizer_sha256), 'AI_BATCH_CONFIG')
            else:
                check(not self.tokenizer_file and not self.tokenizer_sha256, 'AI_BATCH_CONFIG')

    @property
    def input_limit(self):
        return min(self.input_tokens, self.context_tokens - self.output_tokens
                   - (self.context_tokens + 9)//10 - self.overhead_tokens)

    def public(self):
        # Absence retains existing serialized settings and queued job pins.
        return {k:v for k,v in asdict(self).items() if k != 'tokenizer_file'
                and not (k == 'max_concurrency' and v == 1)}

    @classmethod
    def from_env(cls, env):
        prefix = 'SANITY_OA_AI_BATCH_'
        flag = env.get(prefix+'ENABLED','false')
        check(flag in ('true','false'), 'AI_BATCH_CONFIG')
        values = {'enabled':flag=='true'}
        defaults = cls()
        for key in asdict(defaults):
            if key == 'enabled': continue
            raw = env.get(prefix+key.upper(),getattr(defaults,key))
            if type(getattr(defaults,key)) is int:
                check(type(raw) is int or isinstance(raw,str) and raw.isascii() and raw.isdecimal(), 'AI_BATCH_CONFIG')
                raw = int(raw)
            values[key] = raw
        return cls(**values)


@lru_cache(maxsize=4)
def _tokenizer(raw, expected):
    try:
        from tokenizers import Tokenizer
        check(hashlib.sha256(raw).hexdigest() == expected, 'AI_BATCH_TOKENIZER_CHANGED')
        result = Tokenizer.from_str(raw.decode('utf-8'))
        result.no_truncation(); result.no_padding()
        return result
    except ReviewError:
        raise
    except Exception:
        raise ReviewError('AI_BATCH_TOKENIZER_INVALID','Cannot load the configured offline tokenizer') from None


class TokenBudget:
    def __init__(self, settings):
        self.settings = settings
        check(settings.enabled, 'AI_BATCH_CONFIG')
        if settings.tokenizer_mode == 'utf8_bytes':
            self.tokenizer = None
            return
        try:
            with Path(settings.tokenizer_file).open('rb') as stream:
                raw = stream.read(64*1024*1024+1)
        except OSError:
            raise ReviewError('AI_BATCH_TOKENIZER_INVALID','Offline tokenizer is unavailable') from None
        check(len(raw) <= 64*1024*1024, 'AI_BATCH_TOKENIZER_INVALID')
        self.tokenizer = _tokenizer(raw,settings.tokenizer_sha256)

    def tokens(self, text):
        # Explicit conservative estimate, not a claim to know the model's
        # tokenizer. No bytes/4 heuristic or silent fallback from a bad file.
        if self.tokenizer is None: return len(text.encode('utf-8'))
        try: return len(self.tokenizer.encode(text,add_special_tokens=True).ids)
        except Exception:
            raise ReviewError('AI_BATCH_TOKENIZER_INVALID','Cannot count the complete model input') from None

    def measure(self, packet, instruction):
        # Message framing/reasoning overhead is an explicit deployment budget,
        # not an assertion that every compatible model has OpenAI's template.
        return self.measure_serialized(canonical_json(packet), instruction)

    def measure_serialized(self, raw, instruction):
        if self.tokenizer is None:
            return len(instruction.encode('utf-8')) + len(raw)
        return self.tokens(instruction) + self.tokens(raw.decode('utf-8'))

    def check(self, packet, instruction):
        return self.check_serialized(canonical_json(packet), instruction)

    def check_serialized(self, raw, instruction):
        count = self.measure_serialized(raw, instruction)
        check(count <= self.settings.input_limit, 'AI_INPUT_LIMIT', 'Complete batch exceeds its token budget')
        return count
