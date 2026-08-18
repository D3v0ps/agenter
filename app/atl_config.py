"""ATL-gränser (Arbetstidslagen) för kontrollera_atl.

OBS: Dessa värden är försiktiga standardvärden och SKA stämmas av mot
Bemanningsavtalet (och eventuella lokala avtal/kollektivavtalade avvikelser)
innan produktion. Ändringar här slår direkt på vilka erbjudanden som
blockeras.

Kontrollen räknar alltid mot ALLA aktiva bokningar, även importerade
befintliga pass."""

# Minsta sammanhängande dygnsvila (timmar) per 24-timmarsperiod.
DYGNSVILA_TIMMAR = 11
DYGNSPERIOD_TIMMAR = 24

# Minsta sammanhängande veckovila (timmar) per sjudagarsperiod.
VECKOVILA_TIMMAR = 36
VECKOPERIOD_DAGAR = 7

# Tak för total arbetstid (timmar) per rullande sjudagarsperiod.
MAX_ARBETSTID_TIMMAR_PER_VECKOPERIOD = 48
