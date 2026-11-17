"""Every currency a payback may be paid in: the active ISO 4217 codes.

A payback can be handed over in any real currency (ADR-0046), e.g. rupees for a debt kept in
dollars, even if no bill on the trip used it yet. Checking against the real codes still catches
most typos ("SGF", "IRD") instead of saving a currency that doesn't exist.

Kept as a plain list in the repo rather than a library, so nothing new needs installing. If a
new currency is ever issued, add its three letters here.
"""

ISO_4217 = frozenset("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP
BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP
GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR
KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK
MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR
SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS
UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XCG XOF XPF YER ZAR ZMW ZWG
""".split())
