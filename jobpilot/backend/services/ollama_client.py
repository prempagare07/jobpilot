from __future__ import annotations

from backend.agents.ollama_client import FAST_MODEL, SMART_MODEL, OllamaClient, OllamaOfflineError


class ServiceOllamaClient(OllamaClient):
    async def generate(
        self,
        prompt: str,
        model: str = FAST_MODEL,
        system: str | None = None,
        json_mode: bool = False,
    ) -> str:
        if system:
            return await self.chat(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                model=model,
                json_mode=json_mode,
            )
        return await super().generate(prompt=prompt, model=model, json_mode=json_mode)

    async def quick(self, prompt: str, system: str | None = None, json_mode: bool = False) -> str:
        return await self.generate(prompt=prompt, model=FAST_MODEL, system=system, json_mode=json_mode)

    async def smart(self, prompt: str, system: str | None = None, json_mode: bool = False) -> str:
        return await self.generate(prompt=prompt, model=SMART_MODEL, system=system, json_mode=json_mode)


ollama_client = ServiceOllamaClient()

__all__ = ["FAST_MODEL", "SMART_MODEL", "OllamaClient", "OllamaOfflineError", "ollama_client"]
