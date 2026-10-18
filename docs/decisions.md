# Decisions

Results of the execution of the `models-quota.sh` script. It shows for which models I've quota, based for Chat, Reranking and embedding:
```Bash
Suscripción: Azure subscription 1 | Región: eastus2

== CHAT (90) ==
MODELO                                  VERSIÓN     SKU               ESTADO              LÍMITE  USADO  DISPONIBLE
Codestral-2501                          2           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Cohere-command-a-plus-05-2026           1           GlobalStandard    Preview             20.0    0.0    20
DeepSeek-R1                             1           GlobalStandard    Deprecated          20.0    0.0    20
DeepSeek-V3.2                           1           GlobalStandard    GenerallyAvailable  20.0    0.0    20
DeepSeek-V3.2-Speciale                  1           GlobalStandard    GenerallyAvailable  20.0    0.0    20
DeepSeek-V4-Flash                       2026-04-23  DataZoneStandard  GenerallyAvailable  20.0    0.0    20
DeepSeek-V4-Flash                       2026-04-23  GlobalStandard    GenerallyAvailable  20.0    0.0    20
DeepSeek-V4-Flash-0731                  2026-07-31  GlobalStandard    Preview             20.0    0.0    20
DeepSeek-V4-Pro                         2026-04-23  DataZoneStandard  GenerallyAvailable  20.0    0.0    20
DeepSeek-V4-Pro                         2026-04-23  GlobalStandard    GenerallyAvailable  20.0    0.0    20
DeepSeek-V4.1-Flash                     2026-09-10  GlobalStandard    Preview             20.0    0.0    20
Kimi-K2.5                               1           GlobalStandard    Preview             20.0    0.0    20
Kimi-K2.6                               2026-04-20  GlobalStandard    Preview             20.0    0.0    20
Kimi-K2.7-Code                          2026-06-12  GlobalStandard    Preview             20.0    0.0    20
Llama-3.3-70B-Instruct                  1           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Llama-3.3-70B-Instruct                  2           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Llama-3.3-70B-Instruct                  3           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Llama-3.3-70B-Instruct                  4           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Llama-3.3-70B-Instruct                  5           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Llama-3.3-70B-Instruct                  9           DataZoneStandard  GenerallyAvailable  200.0   0.0    200
Llama-3.3-70B-Instruct                  9           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Llama-4-Maverick-17B-128E-Instruct-FP8  1           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Llama-4-Scout-17B-16E-Instruct          1           GlobalStandard    GenerallyAvailable  20.0    0.0    20
MAI-Thinking-1                          2026-06-01  GlobalStandard    Preview             500.0   0.0    500
Ministral-3B                            1           DataZoneStandard  GenerallyAvailable  200.0   0.0    200
Ministral-3B                            1           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Mistral-Large-3                         1           DataZoneStandard  GenerallyAvailable  20.0    0.0    20
Mistral-Large-3                         1           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Mistral-large                           1           GlobalStandard    GenerallyAvailable  600.0   0.0    600
Phi-4                                   2           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Phi-4                                   3           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Phi-4                                   4           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Phi-4                                   5           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Phi-4                                   6           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Phi-4                                   7           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Phi-4-mini-instruct                     1           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Phi-4-mini-reasoning                    1           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Phi-4-multimodal-instruct               1           GlobalStandard    GenerallyAvailable  20.0    0.0    20
Phi-4-reasoning                         1           GlobalStandard    GenerallyAvailable  20.0    0.0    20
cohere-command-a                        1           GlobalStandard    GenerallyAvailable  20.0    0.0    20
gpt-4.1                                 2025-04-14  DeveloperTier     Legacy              250.0   0.0    250
gpt-4.1                                 2025-04-14  GlobalStandard    Legacy              250.0   0.0    250
gpt-4.1                                 2025-04-14  Standard          Legacy              50.0    0.0    50
gpt-4.1-mini                            2025-04-14  DataZoneStandard  Legacy              500.0   0.0    500
gpt-4.1-mini                            2025-04-14  DeveloperTier     Legacy              500.0   0.0    500
gpt-4.1-mini                            2025-04-14  GlobalStandard    Legacy              200.0   0.0    200
gpt-4.1-mini                            2025-04-14  Standard          Legacy              200.0   0.0    200
gpt-4.1-nano                            2025-04-14  DataZoneStandard  Legacy              500.0   0.0    500
gpt-4.1-nano                            2025-04-14  DeveloperTier     Legacy              500.0   0.0    500
gpt-4.1-nano                            2025-04-14  GlobalStandard    Legacy              500.0   0.0    500
gpt-4o                                  2024-05-13  Standard          Deprecating         50.0    0.0    50
gpt-4o                                  2024-08-06  DataZoneStandard  Deprecating         8.0     0.0    8
gpt-4o                                  2024-08-06  GlobalStandard    Deprecating         8.0     0.0    8
gpt-4o                                  2024-08-06  Standard          Deprecating         50.0    0.0    50
gpt-4o                                  2024-11-20  Standard          Legacy              50.0    0.0    50
gpt-4o-mini                             2024-07-18  DataZoneStandard  Deprecating         8.0     0.0    8
gpt-4o-mini                             2024-07-18  GlobalStandard    Deprecating         8.0     0.0    8
gpt-4o-mini                             2024-07-18  Standard          Deprecating         200.0   0.0    200
gpt-5-mini                              2025-08-07  GlobalStandard    GenerallyAvailable  500.0   0.0    500
gpt-5.4-mini                            2026-03-17  DataZoneStandard  GenerallyAvailable  200.0   0.0    200
gpt-audio-mini                          2025-10-06  GlobalStandard    GenerallyAvailable  30.0    0.0    30
gpt-audio-mini                          2025-12-15  GlobalStandard    GenerallyAvailable  30.0    0.0    30
gpt-oss-120b                            1           DataZoneStandard  GenerallyAvailable  5000.0  0.0    5000
gpt-oss-120b                            1           DeveloperTier     GenerallyAvailable  5000.0  0.0    5000
gpt-oss-120b                            1           GlobalStandard    GenerallyAvailable  5000.0  0.0    5000
gpt-oss-20b                             11          DataZoneStandard  GenerallyAvailable  200.0   0.0    200
gpt-oss-20b                             11          DeveloperTier     GenerallyAvailable  200.0   0.0    200
gpt-oss-20b                             11          GlobalStandard    GenerallyAvailable  200.0   0.0    200
grok-4-1-fast-non-reasoning             1           DataZoneStandard  GenerallyAvailable  20.0    0.0    20
grok-4-1-fast-non-reasoning             1           GlobalStandard    GenerallyAvailable  50.0    0.0    50
grok-4-1-fast-reasoning                 1           DataZoneStandard  GenerallyAvailable  20.0    0.0    20
grok-4-1-fast-reasoning                 1           GlobalStandard    GenerallyAvailable  50.0    0.0    50
grok-4-20-non-reasoning                 1           DataZoneStandard  Preview             20.0    0.0    20
grok-4-20-non-reasoning                 1           GlobalStandard    Preview             50.0    0.0    50
grok-4-20-reasoning                     1           DataZoneStandard  Preview             20.0    0.0    20
grok-4-20-reasoning                     1           GlobalStandard    Preview             50.0    0.0    50
grok-4.3                                1           DataZoneStandard  Preview             20.0    0.0    20
grok-4.3                                1           GlobalStandard    Preview             50.0    0.0    50
grok-4.6                                1           GlobalStandard    Preview             50.0    0.0    50
mistral-medium-2505                     1           GlobalStandard    GenerallyAvailable  20.0    0.0    20
mistral-medium-3-5                      1           DataZoneStandard  Preview             20.0    0.0    20
mistral-medium-3-5                      1           GlobalStandard    Preview             20.0    0.0    20
mistral-small-2503                      1           GlobalStandard    GenerallyAvailable  20.0    0.0    20
o4-mini                                 2025-04-16  DataZoneStandard  Deprecating         500.0   0.0    500
o4-mini                                 2025-04-16  DeveloperTier     Deprecating         250.0   0.0    250
o4-mini                                 2025-04-16  GlobalStandard    Deprecating         100.0   0.0    100
o4-mini                                 2025-04-16  Standard          Deprecating         200.0   0.0    200
qwen3-32b                               1           DataZoneStandard  GenerallyAvailable  200.0   0.0    200
qwen3-32b                               1           DeveloperTier     GenerallyAvailable  200.0   0.0    200
qwen3-32b                               1           GlobalStandard    GenerallyAvailable  200.0   0.0    200

== EMBEDDING (10) ==
MODELO                        VERSIÓN  SKU               ESTADO              LÍMITE  USADO  DISPONIBLE
Cohere-Embed-V5-Fast          1        GlobalStandard    Preview             20.0    0.0    20
Cohere-Embed-V5-Pro           1        GlobalStandard    Preview             20.0    0.0    20
Cohere-embed-v3-english       1        GlobalStandard    GenerallyAvailable  600.0   0.0    600
Cohere-embed-v3-multilingual  1        GlobalStandard    GenerallyAvailable  600.0   0.0    600
embed-v-4-0                   1        GlobalStandard    GenerallyAvailable  1000.0  0.0    1000
text-embedding-3-large        1        Standard          GenerallyAvailable  350.0   0.0    350
text-embedding-3-small        1        DataZoneStandard  GenerallyAvailable  1000.0  0.0    1000
text-embedding-3-small        1        GlobalStandard    GenerallyAvailable  1000.0  0.0    1000
text-embedding-3-small        1        Standard          GenerallyAvailable  350.0   0.0    350
text-embedding-ada-002        2        Standard          GenerallyAvailable  350.0   0.0    350

== RERANK (2) ==
MODELO                   VERSIÓN  SKU             ESTADO              LÍMITE  USADO  DISPONIBLE
Cohere-rerank-v4.0-fast  1        GlobalStandard  GenerallyAvailable  20.0    0.0    20
Cohere-rerank-v4.0-pro   1        GlobalStandard  GenerallyAvailable  20.0    0.0    20
```

Elección:
- Chat: gpt-4.1-mini (Modelo de OpenAI más barato)
- Reranker: Cohere-rerank-v4.0-fast (Solo hay dos opciones acá, escogí el más barato)
- Embedding: text-embedding-3-small (segundo mejor modelo de MTEB al que tengo quota)

