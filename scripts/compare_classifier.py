"""Report the task-classifier tier distribution over real Bedrock logs.

Bucket is resolved from --bucket, else BEDROCK_LOG_BUCKET / SSM. Never
hard-coded. Prints the tier distribution and unknown rate from the real
analysis pipeline, a sample of classified cases, and can freeze an
anonymized fixture for regression testing.
"""
import argparse
import json
import re
from collections import Counter

import boto3

from agent.config import AWS_REGION, get_secret
from models.normalization import normalize_principal_arn
from tools.invocation_logs import (
    _list_log_objects, _sample_objects, _parse_log_entries,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", default=get_secret("BEDROCK_LOG_BUCKET"))
    ap.add_argument("--prefix", default=get_secret("BEDROCK_LOG_PREFIX") or "AWSLogs")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--freeze-fixture", metavar="PATH",
                    help="write anonymized sampled entries to PATH")
    args = ap.parse_args()
    if not args.bucket:
        raise SystemExit("No bucket: pass --bucket or set BEDROCK_LOG_BUCKET")

    s3 = boto3.client("s3", region_name=AWS_REGION)
    objs = _list_log_objects(s3, args.bucket, args.prefix, args.days)
    sampled = _sample_objects(objs, args.sample)
    entries = _parse_log_entries(s3, args.bucket, sampled)

    # `_parse_log_entries` runs the real tool pipeline: it fetches offloaded
    # bodies and classifies via effective (cache-aware) size + combined
    # multi-message text, storing the result on each entry. Read the entry's
    # actual classification so the reported distribution matches production.
    dist, examples = Counter(), []
    for e in entries:
        if not e.user_message_text:
            continue
        tier = e.classified_tier or "unknown"
        dist[tier] += 1
        if len(examples) < 15:
            examples.append({
                "text": e.user_message_text[:120],
                "raw_input_tokens": e.input_token_count,
                "cache_read": e.cache_read_tokens,
                "tier": tier,
                "conf": e.classification_confidence,
            })

    total = max(1, sum(dist.values()))
    print("entries:", len(entries))
    print("tier distribution:", dict(dist))
    print("unknown rate:", round(dist.get("unknown", 0) / total, 3))
    print("--- classified examples ---")
    for ex in examples:
        print(json.dumps(ex))

    if args.freeze_fixture:
        # Scrub AWS account IDs from EVERY string field before writing. On real
        # logs modelId is a full inference-profile ARN carrying the account, so
        # scrubbing only iam_principal is not enough. normalize_principal_arn
        # replaces the account segment with a placeholder.
        def scrub(v):
            if not isinstance(v, str) or not v:
                return v
            v = normalize_principal_arn(v)
            # Belt-and-suspenders: replace any residual bare 12-digit AWS
            # account ID that normalize_principal_arn (ARN-only) would miss.
            return re.sub(r"\b\d{12}\b", "<REPLACE-WITH-YOUR-AWS-ACCOUNT>", v)

        frozen = [{
            "model_id": scrub(e.model_id),
            "normalized_model": scrub(e.normalized_model),
            "input_token_count": e.input_token_count,
            "output_token_count": e.output_token_count,
            "user_message_text": scrub(e.user_message_text),
            "message_count": e.message_count,
            "model_tier": e.model_tier,
            "iam_principal": scrub(e.iam_principal or ""),
        } for e in entries if e.user_message_text][:30]
        with open(args.freeze_fixture, "w") as f:
            json.dump(frozen, f, indent=2)
        print(f"wrote {len(frozen)} anonymized entries to {args.freeze_fixture}")


if __name__ == "__main__":
    main()
