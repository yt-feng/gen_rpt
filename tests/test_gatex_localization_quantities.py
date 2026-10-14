"""Offline regressions for exact Chinese quantity rendering and fidelity."""
import unittest

from gen_rpt.gatex_translation_quantities import (
    QuantityFidelityError, protect_quantities, quantities_equivalent,
)


class QuantityFidelityTests(unittest.TestCase):
    def test_spelled_scales_render_natural_chinese_with_exact_arithmetic(self):
        pairs = [
            ("$15 billion", "$150亿"), ("20 million", "2000万"),
            ("1.7 million", "170万"), ("1 million", "100万"),
            ("5 thousand", "5000"), ("1.25 trillion", "1.25万亿"),
            ("£2.9 billion ($3.8 billion)", "£29亿 ($38亿)"),
            ("USD 15 billion", "USD 150亿"),
            (".5 million", "50万"), ("$.5 billion", "$5亿"),
            ("$15 Billion", "$150亿"), ("20 MILLION", "2000万"),
            ("0.12345678901234567890123456789 million", "12.345678901234567890123456789万"),
        ]
        for source, target in pairs:
            with self.subTest(source=source):
                protected = protect_quantities(source)
                self.assertEqual(protected.restore(protected.masked), target)
                self.assertTrue(quantities_equivalent(source, target))

    def test_negative_and_explicit_positive_signs_survive_currency_positions(self):
        pairs = [("-$1 million", "-$100万"), ("-USD .5 billion", "-USD 5亿"),
                 ("$-.5 billion", "$-5亿"), ("-.5 million", "-50万"),
                 ("+USD .5 billion", "+USD 5亿"), ("-0 million", "-0")]
        for source, target in pairs:
            with self.subTest(source=source):
                protected = protect_quantities(source)
                self.assertEqual(protected.restore(protected.masked), target)
                self.assertTrue(quantities_equivalent(source, target))
                self.assertFalse(quantities_equivalent(source, target.replace("-", "").replace("+", "")))

    def test_written_percentage_units_render_without_changing_the_value(self):
        for source, target in [
            ("65 per cent", "65%"), ("5.03 percent", "5.03%"),
            ("42 PER CENT", "42%"), ("3.1 percentage points", "3.1个百分点"),
            ("1 percentage point", "1个百分点"), ("2 percent points", "2个百分点"),
            ("5.03％", "5.03%"), ("+0.05 per cent", "+0.05%"),
            ("-3.1 Percentage Points", "-3.1个百分点"),
            ("3.1 percentage-point", "3.1个百分点"),
            ("3.1-percentage-point", "3.1个百分点"),
            ("20-percent", "20%"),
        ]:
            with self.subTest(source=source):
                protected = protect_quantities(source)
                self.assertEqual(protected.restore(protected.masked), target)
                self.assertTrue(quantities_equivalent(source, target))

    def test_percent_is_not_percentage_points_or_per_mille(self):
        for source, target in [
            ("42 per cent", "42个百分点"), ("3.1 percentage points", "3.1%"),
            ("42 percent", "42‰"), ("42 per cent", "42"),
            ("42", "42%"), ("5.03 percent", "5.030%"),
            ("+0.05 per cent", "0.05%"),
        ]:
            with self.subTest(source=source, target=target):
                self.assertFalse(quantities_equivalent(source, target))

    def test_complete_rate_tokens_and_suffixes_are_not_partially_consumed(self):
        protected = protect_quantities("Inflation is 5.03 per cent; the forecast fell 3.1 percentage points.")
        self.assertEqual([token.original for token in protected.tokens], ["5.03 per cent", "3.1 percentage points"])
        self.assertEqual(protected.restore(protected.masked), "Inflation is 5.03%; the forecast fell 3.1个百分点.")
        self.assertFalse(quantities_equivalent("65 per cent", "65%个百分点"))
        self.assertFalse(quantities_equivalent("3.1 percentage points", "3.1个百分点%"))
        self.assertFalse(quantities_equivalent("The total is 20.", "总数。"))
        self.assertTrue(quantities_equivalent("The total is 20.", "总数是20。"))
        self.assertFalse(quantities_equivalent("The price is $0.10.", "价格是$0.11。"))

    def test_currency_is_bound_to_each_amount_not_just_global_symbol_counts(self):
        self.assertTrue(quantities_equivalent("GBP 2.9 billion", "29亿英镑"))
        self.assertFalse(quantities_equivalent("£2.9 billion", "£2.9十亿美元"))
        self.assertFalse(quantities_equivalent("$1 million £2 million", "$200万 £100万"))
        self.assertFalse(quantities_equivalent("IDR 2 million", "人民币200万"))
        self.assertFalse(quantities_equivalent("$1 million", "100万美元"), "unqualified dollars cannot be assumed to be USD")
        self.assertFalse(quantities_equivalent("¥1 million", "100万日元"), "ambiguous yen/yuan symbols must remain identifiable")

    def test_currency_like_chinese_words_do_not_create_money_tokens(self):
        for source, target in [
            ("Product components are diversified.", "产品元件实现多元化。"),
            ("Chemical elements are diversified.", "化学元素呈现多元化。"),
        ]:
            with self.subTest(target=target):
                self.assertTrue(quantities_equivalent(source, target))
                self.assertEqual(protect_quantities(target).tokens, ())
        self.assertTrue(quantities_equivalent("CNY 1 million", "100万元"))
        self.assertFalse(quantities_equivalent("1 million", "100万元"))

    def test_explicit_us_dollars_keep_identity_and_cannot_lose_the_symbol(self):
        for source, target in [("US$15 billion", "US$150亿"),
                               ("-US$.5 billion", "-US$5亿")]:
            with self.subTest(source=source):
                protected = protect_quantities(source)
                self.assertEqual(protected.restore(protected.masked), target)
                self.assertTrue(quantities_equivalent(source, target))
        self.assertTrue(quantities_equivalent("US$15 billion", "150亿美元"))
        self.assertFalse(quantities_equivalent("US$15 billion", "150亿"))
        self.assertFalse(quantities_equivalent("US$15 billion", "150亿英镑"))
        self.assertFalse(quantities_equivalent("US$15 billion", "$150亿"))
        self.assertFalse(quantities_equivalent("-US$.5 billion", "US$5亿"))

    def test_regional_and_ambiguous_dollar_prefixes_cannot_disappear(self):
        for symbol, code in [("HK$", "HKD"), ("AU$", "AUD"), ("NZ$", "NZD"),
                             ("CA$", "CAD"), ("SG$", "SGD")]:
            with self.subTest(symbol=symbol):
                source = symbol + "15 billion"
                protected = protect_quantities(source)
                self.assertEqual(protected.restore(protected.masked), symbol + "150亿")
                self.assertTrue(quantities_equivalent(source, code + " 150亿"))
                self.assertFalse(quantities_equivalent(source, "150亿"))
                self.assertFalse(quantities_equivalent(source, "USD 150亿"))
        for symbol in ["A$", "C$", "S$"]:
            with self.subTest(symbol=symbol):
                source = symbol + "15 billion"
                self.assertTrue(quantities_equivalent(source, symbol + "150亿"))
                self.assertFalse(quantities_equivalent(source, "150亿"))
                self.assertFalse(quantities_equivalent(source, "USD 150亿"))
        self.assertFalse(quantities_equivalent("C$15 billion", "CAD 150亿"))

    def test_postfix_currency_codes_are_bound_to_the_corresponding_quantity(self):
        source = "1 million USD and 2 million EUR"
        target = "100万 USD and 200万 EUR"
        protected = protect_quantities(source)
        self.assertEqual(protected.restore(protected.masked), target)
        self.assertEqual([token.original for token in protected.tokens],
                         ["1 million USD", "2 million EUR"])
        self.assertTrue(quantities_equivalent(source, target))
        self.assertTrue(quantities_equivalent(source, "100万美元和200万欧元"))
        for altered in ["100万 EUR 和200万 USD", "100万和200万 EUR", "100万美元和200万美元"]:
            with self.subTest(altered=altered):
                self.assertFalse(quantities_equivalent(source, altered))
        for source, target in [("-.5 billion USD", "-5亿 USD"),
                               ("2 million USD/M", "200万 USD/M"),
                               ("2 million/M USD", "200万/M USD")]:
            with self.subTest(source=source):
                protected = protect_quantities(source)
                self.assertEqual(protected.restore(protected.masked), target)
                self.assertTrue(quantities_equivalent(source, target))
        self.assertFalse(quantities_equivalent("2 million USD/M", "200万 EUR/M"))
        self.assertFalse(quantities_equivalent("2 million USD/M", "200万 USD/B"))
        self.assertFalse(quantities_equivalent("USD 1 million", "100万 USD EUR"))
        self.assertFalse(quantities_equivalent("1 USD", "1 EUR"))

    def test_awkward_or_doubled_scales_are_rejected(self):
        for source, target in [("$15 billion", "$15十亿"), ("20 million", "20百万"),
                               ("2 thousand", "20百万元"), ("20", "20百万"),
                               ("$1 million", "$100万美元"), (".5 million", ".500万"),
                               ("$.5 billion", "$.50亿")]:
            with self.subTest(source=source, target=target):
                self.assertFalse(quantities_equivalent(source, target))

    def test_unrelated_plain_numbers_abbreviations_percent_and_codes_are_literal(self):
        source = "Q3 H1 GPT-4.1 3PL USD 347M IDR 2.5T 0012 7.2% $0.10/M"
        protected = protect_quantities(source)
        self.assertEqual(protected.restore(protected.masked), source)
        for old, new in [("Q3", "Q4"), ("H1", "H2"), ("GPT-4.1", "GPT-4.2"),
                         ("3PL", "3PD"), ("347M", "347万"), ("2.5T", "2.5万亿"),
                         ("0012", "12"), ("7.2%", "7.20%"), ("7.2%", "7.2‰"),
                         ("0.10/M", "0.1/M"), ("0.10/M", "0.10/B")]:
            with self.subTest(old=old, new=new):
                self.assertFalse(quantities_equivalent(source, source.replace(old, new)))
        self.assertFalse(quantities_equivalent("20", "0.002万"))

    def test_dates_times_numeric_versions_page_references_and_urls_stay_exact(self):
        source = "2026-07-29 29/07/2026 12:30:45 version 1.2.3 pp. 2–4 https://example.test/v2/2026?a=1 www.brand2.com info2@example.test {report_123}"
        protected = protect_quantities(source)
        self.assertEqual(protected.restore(protected.masked), source)
        self.assertTrue(quantities_equivalent(source, source))
        for old, new in [("2026-07-29", "2026-7-29"), ("29/07/2026", "29/7/2026"),
                         ("12:30:45", "12:30:46"), ("1.2.3", "1.2.4"),
                         ("2–4", "2–5"), ("a=1", "a=2"), ("brand2", "brand3"),
                         ("info2", "info3"), ("report_123", "report_124")]:
            with self.subTest(old=old, new=new):
                self.assertFalse(quantities_equivalent(source, source.replace(old, new)))

    def test_per_unit_suffix_stays_literal_during_scale_conversion(self):
        source, target = "USD 2 million/M", "USD 200万/M"
        protected = protect_quantities(source)
        self.assertEqual(protected.restore(protected.masked), target)
        self.assertTrue(quantities_equivalent(source, target))
        for altered in ["USD 200万/B", "USD 200万/m", "USD 200万", "CNY 200万/M"]:
            self.assertFalse(quantities_equivalent(source, altered))

    def test_ordinal_value_and_role_are_preserved_without_english_suffix(self):
        protected = protect_quantities("The 10th and 21st entries")
        self.assertEqual(protected.restore(protected.masked), "The 第10 and 第21 entries")
        self.assertTrue(quantities_equivalent("10th", "第10"))
        self.assertFalse(quantities_equivalent("10th", "第11"))
        self.assertFalse(quantities_equivalent("10th", "10"))

    def test_missing_duplicated_or_unknown_placeholders_fail_closed(self):
        protected = protect_quantities("Revenue $15 billion in Q3")
        self.assertEqual(len(protected.tokens), 2)
        self.assertEqual(protected.hints[0]["value"], "$150亿")
        for text in ["遗漏数值", protected.masked + protected.tokens[0].placeholder,
                     protected.masked + "{GATEX_QUANTITY_99}"]:
            with self.subTest(text=text):
                with self.assertRaisesRegex(QuantityFidelityError, "^QUANTITY_PLACEHOLDER_MISMATCH$"):
                    protected.restore(text)
        for suffix in ["{GATEX_QUANTITY_x}", "{GATEX_QUANTITY_-1}",
                       "{GATEX_QUANTITY_0 }", "{GATEX_QUANTITY_0"]:
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(QuantityFidelityError, "^QUANTITY_PLACEHOLDER_MISMATCH$"):
                    protected.restore(protected.masked + suffix)

    def test_separated_signs_and_quantity_constraints_are_bound_to_the_amount(self):
        for source, target in [("− 2 million", "-200万"), ("+ 2 million", "+200万"),
                               ("±$1 million", "±$100万"), ("≤$1 million", "≤$100万"),
                               ("EUR ≤2 million", "≤EUR 200万")]:
            with self.subTest(source=source):
                protected = protect_quantities(source)
                self.assertEqual(protected.restore(protected.masked), target)
                self.assertTrue(quantities_equivalent(source, target))
        for source, target in [("− 2 million", "200万"), ("+ 2 million", "200万"),
                               ("±$1 million", "$100万"), ("≤$1 million", "≥$100万"),
                               ("≤$1 million and $2 million", "$100万和≤$200万"),
                               ("EUR ≤2 million GBP ≤3 million", "GBP ≤200万 EUR ≤300万")]:
            with self.subTest(source=source, target=target):
                self.assertFalse(quantities_equivalent(source, target))

    def test_local_arithmetic_order_and_operators_are_not_global_counters(self):
        pairs = [("2 million - 1 million", "200万 -100万"),
                 ("2 million + 1 million", "200万 +100万"),
                 ("2 million * 3", "200万 * 3"),
                 ("2 million ≤ 1 million", "200万 ≤100万"),
                 ("20% - 5 percentage points", "20% - 5个百分点")]
        for source, target in pairs:
            with self.subTest(source=source):
                protected = protect_quantities(source)
                self.assertEqual(len(protected.tokens), 1)
                self.assertEqual(protected.restore(protected.masked), target)
                self.assertTrue(quantities_equivalent(source, target))
        for source, target in [("2 million - 1 million", "200万 +100万"),
                               ("2 million - 1 million", "100万 -200万"),
                               ("2 million * 3", "200万 / 3"),
                               ("2 million ≤ 1 million", "≤100万 200万"),
                               ("20% - 5 percentage points", "20% + 5个百分点"),
                               ("20 - 5", "5 - 20")]:
            with self.subTest(source=source, target=target):
                self.assertFalse(quantities_equivalent(source, target))
        self.assertFalse(quantities_equivalent("pp. 2–4", "页4–2"))

    def test_unidentified_operator_combinations_fail_closed_without_changing_codes(self):
        for source in ["2 million + (rate)", "≤(20)", "≤USD≥1 million"]:
            with self.subTest(source=source):
                with self.assertRaisesRegex(QuantityFidelityError, "^UNSUPPORTED_QUANTITY$"):
                    protect_quantities(source)
                self.assertFalse(quantities_equivalent(source, source))
        source = "Cross-border year-on-year COVID-19 GPT-4.1 2026-07-29 Q3 20-million"
        protected = protect_quantities(source)
        self.assertEqual(protected.restore(protected.masked), source)
        self.assertTrue(quantities_equivalent(source, source))
        self.assertEqual(protect_quantities("Cross-border year-on-year").tokens, ())

    def test_sentence_period_is_not_part_of_a_numeric_code(self):
        for source, target, code in [("The forecast is Q3.", "预测为Q3。", "Q3"),
                                     ("The model is GPT-4.1.", "模型为GPT-4.1。", "GPT-4.1")]:
            with self.subTest(source=source):
                protected = protect_quantities(source)
                self.assertEqual(protected.tokens[0].original, code)
                self.assertEqual(protected.restore(protected.masked), source)
                self.assertTrue(quantities_equivalent(source, target))
                self.assertFalse(quantities_equivalent(source, target.replace("3", "4").replace("4.1", "4.2")))

    def test_source_approximation_and_trailing_lower_bound_are_bound_to_the_quantity(self):
        for source, target in [("~$84+", "~$84+"), ("4+ hours", "4+ 小时"),
                               ("~$1 million+", "~$100万+"), ("~ 4+ hours", "~ 4+ 小时")]:
            with self.subTest(source=source):
                protected = protect_quantities(source)
                self.assertEqual(len(protected.tokens), 1)
                self.assertTrue(quantities_equivalent(source, target))
                self.assertTrue(quantities_equivalent(source, protected.restore(protected.masked)))
        for source, target in [("~$84+", "$84+"), ("~$84+", "~$84"),
                               ("~$84+", "~£84+"), ("4+ hours", "4 hours"),
                               ("~$84+", "≤$84+"), ("4+ hours", "+4 hours"),
                               ("~$84+ and $90", "$84 and ~$90+"),
                               ("~$1 million+ and $2 million", "$100万和~$200万+")]:
            with self.subTest(source=source, target=target):
                self.assertFalse(quantities_equivalent(source, target))
        self.assertEqual(protect_quantities("4 + 5").restore(protect_quantities("4 + 5").masked), "4 + 5")
        self.assertFalse(quantities_equivalent("4 + 5", "4+ 5"))
        with self.assertRaisesRegex(QuantityFidelityError, "UNSUPPORTED_QUANTITY"):
            protect_quantities("4++ hours")

    def test_duplicated_omitted_or_added_quantities_never_pass(self):
        for target in ["收入$150亿与$150亿", "收入", "收入$150亿，新增5"]:
            self.assertFalse(quantities_equivalent("Revenue $15 billion", target))
        self.assertFalse(quantities_equivalent("USD income", "CNY income"))
        self.assertTrue(quantities_equivalent("没有数字", "No numeric tokens"))


if __name__ == "__main__":
    unittest.main()
