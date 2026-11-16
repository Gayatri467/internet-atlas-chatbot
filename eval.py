"""
Evaluate retrieval coverage across all Internet Atlas README files.
This tests document retrieval, not Qwen answer generation.
"""

import app

app.DEBUG = False

TESTS = [
    # Original FRR questions
    ("What are the two ways to run the FRR lab?", ["containerlab", "docker compose"]),
    ("Which routing protocol does the FRR lab use?", ["ebgp"]),
    ("List the MRT dump files produced by FRR.", ["updates", "updates-tx", "routes-mrt"]),
    ("Tell me about FRR MRT", ["frr"]),

    # Docling chatbot
    ("Which tool converts HTML documents into Markdown?", ["docling"]),
    ("Which local language model does the chatbot use?", ["qwen 2.5 1.5b"]),
    ("Which runtime provides the local model API?", ["llama.cpp"]),
    ("What Markdown file is generated from the HTML input?", ["output.md"]),
    ("What command starts the chatbot backend?", ["python document_backend.py"]),

    # FRR MRT lab
    ("How many independent labs are described in the FRR MRT project?", ["operator 1", "operator 2"]),
    ("What FRR image is built for the MRT lab?", ["frr-local:mrt-tx"]),
    ("How frequently do the updates and updates-tx files rotate?", ["every minute"]),

    # Multi-MRT tenancy
    ("What is the tenancy project operating manual about?", ["private-operator bgp slice"]),
    ("How are public and private BGP views combined for a tenant?", ["public", "own"]),
    ("Name one origin validation outcome demonstrated in the project.", ["origin_match"]),
    ("According to the tenancy README, what command runs the complete pipeline?", ["make all"]),
    ("Which graph database does the tenancy pipeline start for each tenant?", ["neo4j"]),

    # Tenancy all-demo
    ("Which protocol should be used to connect to the tenant Neo4j databases?", ["bolt://"]),
    ("What HTTP status does operator2 receive when requesting operator1's private-only prefix through the API?", ["404"]),

    # Single-MRT PoC
    ("What is the overall processing chain in the single-MRT PoC?", ["mrt", "parquet", "neo4j"]),
    ("Which Docker Compose service reads the MRT dump and prints decoded output?", ["mrt-reader"]),
    ("What file does the Parquet converter create?", ["bgp_updates.parquet"]),
    ("What is the relationship type between AutonomousSystem nodes?", ["adjacent_to"]),

    # Graph PoC
    ("Which database is used to explore the graph PoC?", ["neo4j"]),

    # Internet Mapper API
    ("What command runs the complete end-to-end Internet Mapper API demo?", ["make demo"]),
    ("Which health endpoint does the Internet Mapper API expose?", ["/health"]),
    ("What is the endpoint pattern for looking up a prefix?", ["/api/v1/prefix/{p}"]),

    # Out-of-document questions
    ("What is the capital of France?", []),
    ("Write me a poem about the sea", []),
    ("Who won the cricket world cup?", []),
]

passed = 0

for question, expected in TESTS:
    results = app.retrieve(question, top_k=app.TOP_K)

    best_sem = max((r[2] for r in results), default=0.0)
    best_fus = max((r[3] for r in results), default=0.0)

    retrieved_text = " ".join(
        str(app.chunks[result[0]].get("text", ""))
        for result in results
        if isinstance(result[0], int) and 0 <= result[0] < len(app.chunks)
    ).lower()

    if not expected:
        ok = app.is_out_of_document(results)
        stage = "out-of-document gate"
        detail = (
            app.FALLBACK_ANSWER
            if ok
            else "Document retrieval did not reject this question"
        )
    else:
        missing = [
            term for term in expected
            if term.lower() not in retrieved_text
        ]
        ok = not missing
        stage = "retrieval coverage"
        detail = (
            "Expected information found in retrieved chunks"
            if ok
            else "Missing expected term(s): " + ", ".join(missing)
        )

    passed += ok

    print(
        "PASS" if ok else "FAIL",
        f"[{stage}]",
        f"(sem={best_sem:.2f} fus={best_fus:.3f})",
        question,
    )
    print("     ->", detail)

print(
    f"\nRETRIEVAL COVERAGE: {passed}/{len(TESTS)} "
    f"passed ({passed / len(TESTS) * 100:.1f}%)"
)
