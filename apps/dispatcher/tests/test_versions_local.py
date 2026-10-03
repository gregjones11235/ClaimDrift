"""Integration test for apps/dispatcher/versions.py against the LOCAL Elasticsearch + the live bioRxiv API.

Run (WSL, local ES on :9200):
  cd /home/riku_miku/claim_drift && uv run python apps/dispatcher/tests/test_versions_local.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from elasticsearch import AsyncElasticsearch  # noqa: E402

from apps.dispatcher.versions import fetch_first_version  # noqa: E402

ES = os.environ.get("LOCAL_ES", "http://localhost:9200")
TEST_INDEX = "preprints_versions_test"


async def main():
    es = AsyncElasticsearch(ES)
    try:
        if await es.indices.exists(index=TEST_INDEX):
            await es.indices.delete(index=TEST_INDEX)
        await es.indices.create(index=TEST_INDEX, mappings={"properties": {
            "doi": {"type": "keyword"}, "version": {"type": "keyword"}, "source": {"type": "keyword"},
            "is_final_preprint": {"type": "boolean"}, "posted_date": {"type": "date"}, "text_source": {"type": "keyword"}}})
        r0 = "10.1101/2020.01.23.916395"      # Zhao R0: v1 3.30-5.47 -> v2 2.24-3.58
        ace2 = "10.1101/2020.02.27.967760"    # ACE2: v3 is a withdrawal notice
        linton = "10.1101/2020.01.26.20018754"  # medRxiv, 2 versions
        # case 1: v1 already in ES (plus a later version) -> must return the ES v1 without indexing
        await es.index(index=TEST_INDEX, id=f"{linton}::v2", document={"doi": linton, "version": "v2", "source": "medrxiv", "abstract": "v2 text", "is_final_preprint": True})
        await es.index(index=TEST_INDEX, id=f"{linton}::v1", document={"doi": linton, "version": "v1", "source": "medrxiv", "abstract": "API text (latest version)", "is_final_preprint": True})
        # case 2: only v2 in ES -> must fetch v1 from the API and index it
        await es.index(index=TEST_INDEX, id=f"{r0}::v2", document={"doi": r0, "version": "v2", "source": "biorxiv", "abstract": "v2 text", "is_final_preprint": True})
        # case 3: ACE2 only latest (withdrawn) version in ES
        await es.index(index=TEST_INDEX, id=f"{ace2}::v3", document={"doi": ace2, "version": "v3", "source": "biorxiv", "abstract": "withdrawn", "is_final_preprint": True})
        await es.indices.refresh(index=TEST_INDEX)

        failures = 0

        def check(name, cond, detail=""):
            nonlocal failures
            print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
            failures += 0 if cond else 1

        row, info = await fetch_first_version(es, linton, index=TEST_INDEX)
        check("unmarked ES v1 (API text) is refetched from v1 JATS", row and info["source_of_v1"] == "jats" and "persons infected" in (row.get("abstract") or ""), (row or {}).get("abstract", "")[:80])
        row, info = await fetch_first_version(es, linton, index=TEST_INDEX)
        check("JATS-marked ES v1 is reused on the next call", row and info["source_of_v1"] == "es", info)

        row, info = await fetch_first_version(es, r0, index=TEST_INDEX)
        check("missing v1 is fetched from the API", row and row["version"] == "v1" and info["source_of_v1"] == "jats", info)
        check("fetched v1 is the real v1 text (contains 5.47)", row and "5.47" in (row.get("abstract") or ""), (row or {}).get("abstract", "")[:80])
        got = await es.get(index=TEST_INDEX, id=f"{r0}::v1")
        check("fetched v1 is indexed for next time", got["found"])
        check("no withdrawn versions reported for R0", info["withdrawn"] == [], info["withdrawn"])

        row, info = await fetch_first_version(es, ace2, index=TEST_INDEX)
        check("ACE2: v1 chosen, not the withdrawal notice", row and row["version"] == "v1" and "withdraw" not in (row.get("abstract") or "").lower())
        check("ACE2: withdrawn version reported", info["withdrawn"] == ["v3"], info["withdrawn"])
        check("ACE2: version list complete", info["versions"] == ["v1", "v2", "v3"], info["versions"])

        row, info = await fetch_first_version(es, "10.1101/0000.00.00.000000", index=TEST_INDEX)
        check("unknown DOI returns None (no silent fallback)", row is None, info)

        print("ALL PASS" if failures == 0 else f"{failures} FAILURE(S)")
        return failures
    finally:
        await es.indices.delete(index=TEST_INDEX, ignore_unavailable=True)
        await es.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
