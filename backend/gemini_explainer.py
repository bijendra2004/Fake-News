from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger("sachlens.gemini")


class GeminiExplanationError(RuntimeError):
    pass


class GeminiGroundingUnavailableError(GeminiExplanationError):
    """Raised when search grounding fails (billing, quota, unsupported model)."""
    pass


@dataclass(frozen=True)
class ExplanationResult:
    percentage: int
    verdict: str
    explanation: list[str]
    corrected_info: str | None
    sources: list[dict[str, str]] = field(default_factory=list)
    grounded: bool = False
    is_ai_generated: bool = False


class GeminiExplainer:
    def __init__(self) -> None:
        self.api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
        self.model = (os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()
        self.api_version = (os.getenv("GEMINI_API_VERSION") or "v1beta").strip()
        self.fact_check_api_key = (os.getenv("GOOGLE_FACTCHECK_API_KEY") or "").strip()
        self.tavily_api_key = (os.getenv("TAVILY_API_KEY") or "").strip()

        # Groq provider config
        self.llm_provider = (os.getenv("LLM_PROVIDER") or "gemini").strip().lower()
        self.groq_api_key = (os.getenv("GROQ_API_KEY") or "").strip()
        self.groq_model = (os.getenv("GROQ_MODEL") or "llama-3.3-70b-versatile").strip()

        logger.info(
            "LLM provider=%s, groq_model=%s, gemini_model=%s",
            self.llm_provider, self.groq_model, self.model,
        )

    def ensure_configured(self) -> None:
        if not self.groq_api_key and not self.api_key:
            raise GeminiExplanationError("Neither GEMINI_API_KEY nor GROQ_API_KEY is configured")

    def explain(self, text: str, classifier_signal: dict[str, Any]) -> ExplanationResult:
        # Run Tavily search and Google Fact Check API concurrently
        import concurrent.futures
        tavily_results: list[dict[str, Any]] = []
        fact_check_results: list[dict[str, str]] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            tavily_future = executor.submit(self._search_tavily, text)
            fact_check_future = executor.submit(self._query_fact_check_api, text)
            try:
                tavily_results = tavily_future.result(timeout=6)
            except Exception as e:
                logger.warning("Tavily search parallel task failed: %s", e)
                tavily_results = []
            try:
                fact_check_results = fact_check_future.result(timeout=6)
            except Exception as e:
                logger.warning("Fact Check parallel task failed: %s", e)
                fact_check_results = []

        grounded = len(tavily_results) > 0
        sources: list[dict[str, str]] = [
            {"title": r.get("title", ""), "url": r.get("url", "")}
            for r in tavily_results if r.get("url")
        ]
        if grounded:
            logger.info("Tavily search returned %d results for grounding", len(tavily_results))
        else:
            logger.info("Tavily search returned no results — proceeding without grounding")

        # --- Build prompt with Tavily context injected ---
        prompt = self._build_prompt(
            text, classifier_signal,
            fact_check_results=fact_check_results,
            web_search_results=tavily_results,
        )

        # --- LLM call with fallback across providers and heuristic fallback ---
        try:
            text_output = self._call_llm(prompt, temperature=0.2)
            logger.info("LLM raw text output: %s", text_output[:500])
            parsed = self._parse_response_json(text_output)
            return self._validate_response(parsed, sources=sources, grounded=grounded)
        except Exception as exc:
            logger.warning("All LLM reasoning providers failed: %s. Using heuristic fallback.", exc)
            confidence = float(classifier_signal.get("confidence", 0.5))
            label = str(classifier_signal.get("label", "NEEDS_REVIEW")).upper()
            pct = int(round(confidence * 100)) if label in ("LIKELY_REAL", "REAL") else int(round((1 - confidence) * 100))
            if label not in ("LIKELY_REAL", "LIKELY_FAKE", "NEEDS_REVIEW", "INSUFFICIENT_EVIDENCE"):
                label = "NEEDS_REVIEW"
                pct = 50

            return ExplanationResult(
                percentage=pct,
                verdict=label,
                explanation=[
                    "Automated ML classification model evaluated this statement.",
                    "Live external reasoning was unavailable or rate-limited; baseline statistical heuristics were applied.",
                ],
                corrected_info=None,
                sources=sources,
                grounded=grounded,
            )

    def _search_tavily(self, query: str) -> list[dict[str, Any]]:
        """Search the web using Tavily API for real-time grounding context.

        Returns a list of result dicts with keys: title, url, content.
        Returns [] on any failure so the caller can fall back gracefully.
        """
        if not self.tavily_api_key:
            logger.info("TAVILY_API_KEY not configured — skipping web search grounding")
            return []

        payload = {
            "api_key": self.tavily_api_key,
            "query": query[:400],  # Tavily query limit
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": False,
        }

        try:
            req = urllib.request.Request(
                "https://api.tavily.com/search",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))

            results = data.get("results", [])
            logger.info(
                "Tavily search succeeded: %d results for query=%s",
                len(results), query[:80],
            )
            return results

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="ignore") if e.fp else ""
            logger.warning(
                "Tavily search HTTP error: status=%s body=%s — proceeding without web grounding",
                e.code, error_body[:300],
            )
            return []
        except Exception as e:
            logger.warning(
                "Tavily search failed: %s — proceeding without web grounding", e,
            )
            return []

    def _call_llm(self, prompt: str, *, temperature: float = 0.2) -> str:
        """Route LLM call with automatic cross-provider fallback (Groq <-> Gemini)."""
        primary = self.llm_provider
        last_err: Exception | None = None

        if primary == "groq" and self.groq_api_key:
            try:
                return self._call_groq(prompt, temperature=temperature)
            except Exception as e:
                logger.warning("Groq provider failed (%s), attempting Gemini fallback", e)
                last_err = e
                if self.api_key:
                    return self._call_gemini_raw(prompt, temperature=temperature)
                raise
        elif self.api_key:
            try:
                return self._call_gemini_raw(prompt, temperature=temperature)
            except Exception as e:
                logger.warning("Gemini provider failed (%s), attempting Groq fallback", e)
                last_err = e
                if self.groq_api_key:
                    return self._call_groq(prompt, temperature=temperature)
                raise
        elif self.groq_api_key:
            return self._call_groq(prompt, temperature=temperature)
        else:
            raise GeminiExplanationError("No valid LLM credentials configured (Gemini/Groq)")

    def _call_gemini_raw(self, prompt: str, *, temperature: float = 0.2) -> str:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        raw = self._request_with_fallback(
            json.dumps(payload).encode("utf-8"),
            grounding_active=False,
        )
        response_payload = json.loads(raw)
        return self._extract_text_output(response_payload)

    def _call_groq(self, prompt: str, *, temperature: float = 0.2) -> str:
        """Call Groq's OpenAI-compatible chat completions API with multi-model rate-limit fallback."""
        models_to_try: list[str] = []
        for m in [self.groq_model, "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "gemma2-9b-it"]:
            norm = m.strip()
            if norm and norm not in models_to_try:
                models_to_try.append(norm)

        last_error: Exception | None = None

        for model_name in models_to_try:
            payload = {
                "model": model_name,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a factual verification assistant. "
                            "Output ONLY valid JSON. No markdown, no backticks, no thinking text, no preamble. "
                            "Start your response directly with the opening { brace."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": temperature,
                "max_tokens": 3072,
            }

            for attempt in range(2):
                try:
                    req = urllib.request.Request(
                        "https://api.groq.com/openai/v1/chat/completions",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {self.groq_api_key}",
                            "User-Agent": "SachLens/1.0",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=15) as response:
                        data = json.loads(response.read().decode("utf-8"))

                    content = data["choices"][0]["message"]["content"]
                    logger.info("Groq API succeeded on model=%s (attempt %d)", model_name, attempt + 1)
                    return content

                except urllib.error.HTTPError as e:
                    error_body = e.read().decode("utf-8", errors="ignore") if e.fp else ""
                    logger.warning(
                        "Groq API error on model=%s: HTTP %s (%s) — trying fallback model",
                        model_name, e.code, error_body[:150],
                    )
                    last_error = e
                    # Break attempt loop to switch to next fallback model immediately
                    break
                except (TimeoutError, OSError) as e:
                    last_error = e
                    logger.warning("Groq API timeout on model=%s (attempt %d/2): %s", model_name, attempt + 1, e)
                    continue
                except Exception as e:
                    logger.exception("Groq API request failed on model=%s", model_name)
                    last_error = e
                    break

        raise GeminiExplanationError(f"Groq API failed across all available models: {last_error}") from last_error

    def _request_with_fallback(
        self, request_body: bytes, *, grounding_active: bool = False
    ) -> str:
        models_to_try: list[str] = []
        for model_name in [
            self.model,
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-flash-latest",
            "gemini-1.5-flash-8b",
            "gemini-2.0-flash",
        ]:
            normalized = model_name.strip()
            if normalized and normalized not in models_to_try:
                models_to_try.append(normalized)

        last_error: Exception | None = None
        for model_name in models_to_try:
            request_url = (
                f"https://generativelanguage.googleapis.com/{self.api_version}/models/{model_name}:generateContent"
                f"?key={self.api_key}"
            )
            logger.info("Calling Gemini API endpoint: %s (grounding=%s)", request_url.split("?")[0], grounding_active)
            try:
                req = urllib.request.Request(
                    request_url,
                    data=request_body,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=30) as response:
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as error:
                error_body = error.read().decode("utf-8", errors="ignore") if error.fp else ""
                logger.error(
                    "GEMINI API ATTEMPT FAILED -> model=%s status_code=%s body=%s",
                    model_name, error.code, error_body[:200],
                )
                last_error = error

                if grounding_active and error.code in (400, 403, 429):
                    raise GeminiGroundingUnavailableError(
                        f"Grounding failed with HTTP {error.code}: {error_body}"
                    ) from error

                continue
            except Exception as error:
                logger.exception("Gemini API request failed for model=%s", model_name)
                last_error = error
                continue

        raise GeminiExplanationError("Gemini API request failed for all configured models") from last_error

    def _self_verify(
        self,
        first_pass: ExplanationResult,
        original_claim: str,
        sources: list[dict[str, str]],
    ) -> ExplanationResult | None:
        """Send the first-pass answer back to Gemini for critical self-verification.

        Returns a revised ExplanationResult, or None if verification couldn't run
        (e.g. 429 rate limit), in which case the caller should use the first pass.
        """
        import datetime
        current_date = datetime.datetime.now().strftime("%Y-%m-%d")

        sources_text = ""
        if sources:
            source_lines = [f"  - {s.get('title', '?')}: {s.get('url', '?')}" for s in sources]
            sources_text = "\nGrounding sources used:\n" + "\n".join(source_lines)

        verify_prompt = (
            f"Current Date: {current_date}\n"
            "You are a critical fact-check reviewer. A fact-check assistant produced the following "
            "draft analysis. Your job is to critically re-examine it and revise if needed.\n\n"
            f"Original claim: \"{original_claim}\"\n\n"
            f"Draft analysis:\n"
            f"  percentage: {first_pass.percentage}\n"
            f"  verdict: {first_pass.verdict}\n"
            f"  explanation: {json.dumps(first_pass.explanation)}\n"
            f"  corrected_info: {first_pass.corrected_info}\n"
            f"{sources_text}\n\n"
            "Critically re-examine this:\n"
            "- Are the sources actually relevant and reliable?\n"
            "- FOR IPL 2026 AND UNDECIDED EVENTS: The IPL 2026 tournament has NOT taken place or concluded yet. The winner of IPL 2026 is not yet decided. If the draft states that IPL 2026 has not taken place yet or the winner is undecided, PRESERVE that statement. Do NOT claim the tournament concluded in March-May.\n"
            "- If the sources are weak, conflicting, or absent, and you aren't genuinely confident, "
            "change the verdict to INSUFFICIENT_EVIDENCE with percentage 50.\n\n"
            "Output ONLY the revised JSON with the same schema:\n"
            "{\n"
            '  "percentage": <integer 0-100>,\n'
            '  "verdict": <"LIKELY_REAL" | "LIKELY_FAKE" | "NEEDS_REVIEW" | "INSUFFICIENT_EVIDENCE">,\n'
            '  "explanation": <array of 2-5 short bullet-style strings>,\n'
            '  "corrected_info": <string or null>\n'
            "}\n"
            "- No markdown. No backticks. JSON only.\n"
        )

        try:
            text_output = self._call_llm(verify_prompt, temperature=0.1)
        except (GeminiExplanationError, GeminiGroundingUnavailableError) as err:
            logger.warning("Self-verification call failed (%s) — using first-pass result", err)
            return None

        try:
            parsed = self._parse_response_json(text_output)
            verified = self._validate_response(
                parsed,
                sources=first_pass.sources,
                grounded=first_pass.grounded,
            )
        except (GeminiExplanationError, json.JSONDecodeError) as err:
            logger.warning("Self-verification parse failed (%s) — using first-pass result", err)
            return None

        # Log whether the self-verification changed anything
        changed = (
            verified.percentage != first_pass.percentage
            or verified.verdict != first_pass.verdict
        )
        logger.info(
            "Self-verification %s the answer (first: %d/%s → final: %d/%s)",
            "CHANGED" if changed else "CONFIRMED",
            first_pass.percentage, first_pass.verdict,
            verified.percentage, verified.verdict,
        )
        return verified

    def _query_fact_check_api(self, claim_text: str) -> list[dict[str, str]]:
        """Query Google Fact Check Tools API for existing ClaimReview results."""
        if not self.fact_check_api_key:
            logger.info("GOOGLE_FACTCHECK_API_KEY not configured — skipping Fact Check API")
            return []

        encoded_query = urllib.parse.quote(claim_text[:200], safe="")
        url = (
            f"https://factchecktools.googleapis.com/v1alpha1/claims:search"
            f"?query={encoded_query}&languageCode=en&pageSize=5"
            f"&key={self.fact_check_api_key}"
        )
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as e:
            logger.warning("Fact Check API call failed: %s", e)
            return []

        results: list[dict[str, str]] = []
        claims = data.get("claims", [])
        if not isinstance(claims, list):
            return results

        for claim in claims[:3]:  # top 3 most relevant
            claim_text_found = claim.get("text", "")
            reviews = claim.get("claimReview", [])
            if not isinstance(reviews, list):
                continue
            for review in reviews[:1]:  # first review per claim
                results.append({
                    "claim": claim_text_found,
                    "publisher": review.get("publisher", {}).get("name", "Unknown"),
                    "rating": review.get("textualRating", "Unknown"),
                    "url": review.get("url", ""),
                    "title": review.get("title", ""),
                })

        logger.info("Fact Check API returned %d results for claim", len(results))
        return results

    def _build_prompt(
        self,
        text: str,
        classifier_signal: dict[str, Any],
        *,
        fact_check_results: list[dict[str, str]] | None = None,
        web_search_results: list[dict[str, Any]] | None = None,
    ) -> str:
        import datetime
        current_date = datetime.datetime.now().strftime("%Y-%m-%d")

        # --- Web search context from Tavily ---
        web_search_section = ""
        if web_search_results:
            lines = ["\n--- LIVE WEB SEARCH RESULTS (retrieved just now) ---"]
            for i, r in enumerate(web_search_results, 1):
                lines.append(
                    f"{i}. [{r.get('title', 'Untitled')}]({r.get('url', '')})\n"
                    f"   {r.get('content', '')[:300]}"
                )
            lines.append(
                "\n--- END OF WEB SEARCH RESULTS ---\n"
                "IMPORTANT: You have REAL, CURRENT web search results above. "
                "Use this information to ground your analysis. "
                "Do NOT say 'I don't have live access' or 'I cannot verify this in real-time' — "
                "you have real search results right here. "
                "If the search results don't contain relevant info for the claim, say so specifically "
                "(e.g. 'search results did not contain information about X') rather than giving a generic disclaimer.\n"
            )
            web_search_section = "\n".join(lines)

        # --- Fact check API results ---
        fact_check_section = ""
        if fact_check_results:
            lines = ["\nExisting fact-check verdicts from professional fact-checkers:"]
            for fc in fact_check_results:
                lines.append(
                    f"- {fc.get('publisher', '?')}: \"{fc.get('claim', '?')}\" → {fc.get('rating', '?')}"
                    f" (source: {fc.get('url', 'N/A')})"
                )
            lines.append(
                "Give strong weight to these professional fact-checker verdicts when determining your answer.\n"
            )
            fact_check_section = "\n".join(lines)

        return (
            f"Current Date: {current_date}\n"
            "You are an expert fact-checker and synthetic media (AI/Deepfake) verification assistant.\n"
            "Analyze the user claim/link/media and output ONLY valid JSON with this exact schema:\n"
            "{\n"
            '  "percentage": <integer 0-100: 0-25 for fake/AI generated, 75-100 for verified real>,\n'
            '  "verdict": <"LIKELY_REAL" | "LIKELY_FAKE" | "AI_GENERATED" | "NEEDS_REVIEW" | "INSUFFICIENT_EVIDENCE">,\n'
            '  "is_ai_generated": <true if the video, audio, image, or claim involves AI generation, synthetic media, deepfakes, or voice cloning; false otherwise>,\n'
            '  "explanation": <array of 2-5 short bullet-style strings explaining the verdict and explicitly stating if/why it is AI-generated, fake, or real>,\n'
            '  "corrected_info": <string with correct fact or null>\n'
            "}\n\n"
            "LANGUAGE MATCHING RULE (MANDATORY - MIRROR USER'S INPUT LANGUAGE):\n"
            "- You MUST write all 'explanation' bullets and 'corrected_info' in the exact same language and dialect as the user's input claim:\n"
            "  * If the user wrote in HINGLISH (Hindi written using English/Latin alphabet, e.g., 'kya ye sach hai', 'ye video real hai ya fake', 'modi ji ne bola kya'): Write all explanation bullet points and corrected_info entirely in natural, conversational HINGLISH (e.g., 'Ye video poori tarah se AI-generated deepfake hai aur real footage nahi hai.', 'Official sources ne confirm kiya hai ki...').\n"
            "  * If the user wrote in ENGLISH: Write all explanation bullet points and corrected_info in standard ENGLISH.\n"
            "  * If the user wrote in HINDI (Devanagari script, e.g., 'क्या यह खबर सच है'): Write in clear HINDI.\n"
            "  * If the user wrote in another language (e.g. Marathi, Tamil, Bengali, Telugu): Mirror that language.\n"
            "- Always keep the JSON keys (\"percentage\", \"verdict\", \"is_ai_generated\", \"explanation\", \"corrected_info\") and verdict values in English uppercase as specified.\n\n"
            "CRITICAL CLASSIFICATION & VERDICT RULES:\n"
            "1. AI_GENERATED verdict (is_ai_generated: true):\n"
            "   - Use this verdict whenever the content, video, image, or audio clip is created, synthesized, or manipulated by Artificial Intelligence (e.g., AI video generation via Sora/Runway/Pika, Deepfake voice clone, AI avatar, synthetic CGI presented as real footage, Midjourney/Flux image presented as real, AI face-swapping).\n"
            "   - In the explanation bullets, explicitly state that this is an AI-generated video/image/audio and NOT real footage.\n"
            "2. LIKELY_FAKE verdict (is_ai_generated: false):\n"
            "   - Use this verdict when the claim or video is FALSE, fabricated, out of context, miscaptioned old footage, or misinformation, BUT is NOT created by generative AI tools.\n"
            "3. LIKELY_REAL verdict (is_ai_generated: false):\n"
            "   - Use this verdict when the claim/media is authentic, verified by credible reporting, and true.\n"
            "4. INSUFFICIENT_EVIDENCE / NEEDS_REVIEW:\n"
            "   - Use if evidence is insufficient or mixed. Set percentage 50.\n"
            "5. If the claim is factually false or AI-generated, provide concise corrected facts in corrected_info in the matching language.\n"
            "6. Output ONLY valid JSON starting directly with {.\n"
            f"{web_search_section}"
            f"{fact_check_section}\n"
            f"User claim: {text}\n"
            f"Classifier signal (for context only): {json.dumps(classifier_signal)}\n"
        )

    def _extract_text_output(self, payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise GeminiExplanationError("Gemini returned no candidates")

        first = candidates[0]
        content = first.get("content", {})
        parts = content.get("parts", [])
        texts: list[str] = []
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict):
                    part_text = part.get("text")
                    if isinstance(part_text, str):
                        texts.append(part_text)
        if not texts:
            raise GeminiExplanationError("Gemini returned no text content")
        return "\n".join(texts).strip()

    def _extract_grounding_sources(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        """Extract grounding source citations from the Gemini response."""
        sources: list[dict[str, str]] = []
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return sources

        first = candidates[0]
        grounding_metadata = first.get("groundingMetadata", {})
        if not isinstance(grounding_metadata, dict):
            return sources

        # Extract from groundingChunks (primary source list)
        chunks = grounding_metadata.get("groundingChunks", [])
        if isinstance(chunks, list):
            for chunk in chunks:
                if isinstance(chunk, dict):
                    web = chunk.get("web", {})
                    if isinstance(web, dict):
                        uri = web.get("uri", "")
                        title = web.get("title", "")
                        if uri:
                            sources.append({"url": uri, "title": title or uri})

        # Also check groundingSupports for more detailed attribution
        supports = grounding_metadata.get("groundingSupports", [])
        if isinstance(supports, list):
            for support in supports:
                if isinstance(support, dict):
                    segment = support.get("segment", {})
                    indices = support.get("groundingChunkIndices", [])
                    # These reference the chunks above — already captured

        # Deduplicate by URL
        seen_urls: set[str] = set()
        unique_sources: list[dict[str, str]] = []
        for src in sources:
            if src["url"] not in seen_urls:
                seen_urls.add(src["url"])
                unique_sources.append(src)

        return unique_sources

    def _parse_response_json(self, output: str) -> dict[str, Any]:
        cleaned = output.strip()

        # Strip <think>...</think> blocks (Qwen/reasoning models)
        cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()

        # Strip markdown code fences
        if "```" in cleaned:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
            if match:
                cleaned = match.group(1).strip()
            else:
                cleaned = cleaned.replace("```json", "").replace("```", "").strip()

        # Try to extract JSON object if there's surrounding text
        if not cleaned.startswith("{"):
            match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if match:
                cleaned = match.group(0)

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            # Attempt JSON auto-repair for truncated output (e.g. unclosed array or braces)
            try:
                repaired = cleaned
                if repaired.count('"') % 2 != 0:
                    repaired += '"'
                if repaired.count('[') > repaired.count(']'):
                    repaired += ']'
                if repaired.count('{') > repaired.count('}'):
                    repaired += '}'
                parsed = json.loads(repaired)
            except Exception:
                # Regex fallback extraction
                pct_match = re.search(r'"percentage"\s*:\s*(\d+)', cleaned)
                verdict_match = re.search(r'"verdict"\s*:\s*"([^"]+)"', cleaned)
                expl_match = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', cleaned)
                if pct_match and verdict_match:
                    parsed = {
                        "percentage": int(pct_match.group(1)),
                        "verdict": verdict_match.group(1),
                        "explanation": [e for e in expl_match if len(e) > 15 and e != verdict_match.group(1)][:4] or ["Analysis based on available search evidence."],
                        "corrected_info": None,
                    }
                else:
                    raise GeminiExplanationError(
                        f"LLM returned invalid JSON: {cleaned[:200]}"
                    )

        if not isinstance(parsed, dict):
            raise GeminiExplanationError("LLM response JSON must be an object")
        return parsed

    def _validate_response(
        self,
        payload: dict[str, Any],
        *,
        sources: list[dict[str, str]] | None = None,
        grounded: bool = False,
    ) -> ExplanationResult:
        percentage_raw = payload.get("percentage")
        verdict_raw = payload.get("verdict")
        explanation_raw = payload.get("explanation")
        corrected_info_raw = payload.get("corrected_info")

        try:
            percentage = int(percentage_raw)
        except (TypeError, ValueError) as error:
            raise GeminiExplanationError("Gemini percentage is invalid") from error
        percentage = max(0, min(100, percentage))

        if not isinstance(verdict_raw, str) or not verdict_raw.strip():
            raise GeminiExplanationError("Gemini verdict is invalid")
        verdict = verdict_raw.strip().upper().replace(" ", "_")

        if not isinstance(explanation_raw, list):
            raise GeminiExplanationError("Gemini explanation must be a list")
        explanation: list[str] = []
        for item in explanation_raw:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    explanation.append(text)
        if not explanation:
            raise GeminiExplanationError("Gemini explanation list is empty")

        corrected_info: str | None
        if corrected_info_raw is None:
            corrected_info = None
        elif isinstance(corrected_info_raw, str) and corrected_info_raw.strip():
            corrected_info = corrected_info_raw.strip()
        else:
            corrected_info = None

        is_ai_raw = payload.get("is_ai_generated")
        is_ai_generated = bool(is_ai_raw) or verdict in {"AI_GENERATED", "DEEPFAKE", "SYNTHETIC_MEDIA", "AI_GENERATED_MEDIA"}
        if is_ai_generated:
            verdict = "AI_GENERATED"

        return ExplanationResult(
            percentage=percentage,
            verdict=verdict,
            explanation=explanation,
            corrected_info=corrected_info,
            sources=sources or [],
            grounded=grounded,
            is_ai_generated=is_ai_generated,
        )
