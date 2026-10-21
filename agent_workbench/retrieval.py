"""Permission-first chunk retrieval with an explainable BM25 ranking baseline."""

import math
import re
import unicodedata
from collections import Counter


def terms(text):
    normalized = unicodedata.normalize("NFKC", text).casefold()
    result = re.findall(r"[a-z0-9]+", normalized)
    for sequence in re.findall(r"[\u4e00-\u9fff]+", normalized):
        result.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return [term for term in result if term not in {"如何", "怎么", "请问", "一下", "可以"}]


def split_document(document, size=600, overlap=80):
    content = document["content"]
    for start in range(0, len(content), size - overlap):
        text = content[start : start + size]
        if not text.strip():
            continue
        yield {
            "chunk_id": f"{document['id']}:v{document['version']}:{start}",
            "document_id": document["id"],
            "version": document["version"],
            "title": document["title"],
            "text": text,
            "offset": start,
        }
        if start + size >= len(content):
            break


class Retriever:
    def __init__(self, store):
        self.store = store

    def search(self, query, user, top_k=4):
        # Only accessible documents contribute to either results or IDF statistics.
        chunks = [
            chunk for document in self.store.documents(user) for chunk in split_document(document)
        ]
        query_terms = set(terms(query))
        if not query_terms or not chunks:
            return []
        counters = [
            Counter(terms(chunk["title"] + " " + chunk["title"] + " " + chunk["text"]))
            for chunk in chunks
        ]
        average_length = sum(sum(counter.values()) for counter in counters) / len(counters) or 1
        document_frequency = Counter(term for counter in counters for term in counter)
        results = []
        for chunk, counter in zip(chunks, counters):
            score = 0.0
            length = sum(counter.values())
            for term in query_terms & counter.keys():
                frequency = counter[term]
                idf = math.log(
                    1
                    + (len(chunks) - document_frequency[term] + 0.5)
                    / (document_frequency[term] + 0.5)
                )
                score += (
                    idf
                    * frequency
                    * 2.5
                    / (frequency + 1.5 * (0.25 + 0.75 * length / average_length))
                )
            matched = query_terms & counter.keys()
            title_match = query_terms & set(terms(chunk["title"]))
            if score > 0 and (title_match or len(matched) / len(query_terms) >= 0.25):
                results.append(chunk | {"score": round(score, 4)})
        results.sort(key=lambda hit: (-hit["score"], hit["chunk_id"]))
        return results[:top_k]
