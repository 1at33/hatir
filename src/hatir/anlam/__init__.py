"""Anlam araması: not kasası için embedding tabanlı arama, context ile RRF füzyonu ve yeniden sıralama.

Modüller: anlam (dizin, arama, RRF), rerank (OpenAI uyumlu sohbet modeliyle yeniden sıralama), gemini / nebius /
uyumlu (gömme istemcileri). Komut: `hatir-anlam dizin|ara [--karma|--rerank]|durum`."""
