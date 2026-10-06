"""ratecard.core 的验收测试。

只断言期望的账单结果与不变量：档位边界归属、逐段单价与金额、跨段总价与
各段金额之和、折扣的生效次序与基数、最低消费、封顶、异常输入。
"""

import unittest

from ratecard import (
    KIND_FLAT,
    KIND_PERCENT,
    Discount,
    RateCard,
    round_half_up,
    segment_amount,
    split_usage,
    validate_tiers,
)

TIERS = [(100, 300), (500, 200), (None, 100)]
FLAT_CARD = [(None, 10000)]


class RoundingTest(unittest.TestCase):
    """分位舍入：四舍五入到分，半数进位。"""

    def test_01_round_half_up_to_cents(self):
        self.assertEqual(round_half_up(0, 2), 0)
        self.assertEqual(round_half_up(1, 2), 1)
        self.assertEqual(round_half_up(1, 3), 0)
        self.assertEqual(round_half_up(2, 3), 1)
        self.assertEqual(round_half_up(149, 100), 1)
        self.assertEqual(round_half_up(150, 100), 2)
        self.assertEqual(round_half_up(151, 100), 2)
        self.assertEqual(round_half_up(249, 100), 2)
        self.assertEqual(round_half_up(250, 100), 3)
        self.assertEqual(segment_amount(1, 250), 3)
        self.assertEqual(segment_amount(3, 250), 8)
        self.assertEqual(segment_amount(4, 249), 10)
        self.assertEqual(segment_amount(7, 150), 11)
        self.assertEqual(segment_amount(0, 999), 0)


class SplitTest(unittest.TestCase):
    """用量切分：档位区间左闭右开，边界上的用量只归一段。"""

    def test_02_usage_splits_at_tier_boundaries(self):
        self.assertEqual(split_usage(0, TIERS), [0, 0, 0])
        self.assertEqual(split_usage(1, TIERS), [1, 0, 0])
        self.assertEqual(split_usage(99, TIERS), [99, 0, 0])
        self.assertEqual(split_usage(100, TIERS), [100, 0, 0])
        self.assertEqual(split_usage(101, TIERS), [100, 1, 0])
        self.assertEqual(split_usage(499, TIERS), [100, 399, 0])
        self.assertEqual(split_usage(500, TIERS), [100, 400, 0])
        self.assertEqual(split_usage(501, TIERS), [100, 400, 1])
        self.assertEqual(split_usage(760, TIERS), [100, 400, 260])
        for usage in range(0, 900, 7):
            self.assertEqual(sum(split_usage(usage, TIERS)), usage)


class PricingTest(unittest.TestCase):
    """逐段计价：每段按自己的单价算，段金额相加就是账单小计。"""

    def test_03_each_tier_prices_its_own_share(self):
        card = RateCard(TIERS)
        bill = card.bill(60)
        self.assertEqual(bill["segments"], [
            {"index": 0, "units": 60, "unit_price": 300, "amount": 180},
            {"index": 1, "units": 0, "unit_price": 200, "amount": 0},
            {"index": 2, "units": 0, "unit_price": 100, "amount": 0},
        ])
        self.assertEqual(bill["subtotal"], 180)
        self.assertEqual(bill["total"], 180)
        self.assertEqual(card.bill(100)["subtotal"], 300)
        self.assertEqual(card.bill(100)["total"], 300)
        self.assertEqual(card.bill(250)["subtotal"], 600)
        self.assertEqual(card.bill(700)["total"], 1300)
        for usage in (0, 1, 100, 250, 501, 700):
            current = card.bill(usage)
            self.assertEqual(current["subtotal"],
                             sum(row["amount"] for row in current["segments"]))
            self.assertEqual(current["total"], current["subtotal"])


class CrossTierTest(unittest.TestCase):
    """跨段对账：总价等于各段金额之和，段金额各自半数进位。"""

    def test_04_cross_tier_total_equals_segment_amounts(self):
        card = RateCard([(1, 150), (None, 150)])
        bill = card.bill(2)
        self.assertEqual(bill["segments"], [
            {"index": 0, "units": 1, "unit_price": 150, "amount": 2},
            {"index": 1, "units": 1, "unit_price": 150, "amount": 2},
        ])
        self.assertEqual(bill["subtotal"], 4)
        self.assertEqual(bill["total"], 4)
        self.assertEqual(bill["subtotal"],
                         sum(row["amount"] for row in bill["segments"]))
        three = RateCard([(1, 150), (2, 150), (None, 150)]).bill(3)
        self.assertEqual([row["units"] for row in three["segments"]], [1, 1, 1])
        self.assertEqual(three["subtotal"], 6)
        self.assertEqual(three["total"], 6)


class MinimumTest(unittest.TestCase):
    """最低消费：折后金额不足最低消费的按最低消费收。"""

    def test_05_minimum_charge_applies_after_discounts(self):
        card = RateCard(FLAT_CARD, minimum=800)
        card.add_discount("half", KIND_PERCENT, 5000)
        bill = card.bill(10)
        self.assertEqual(bill["subtotal"], 1000)
        self.assertEqual(bill["discounts"][0]["reduction"], 500)
        self.assertEqual(bill["total"], 800)
        self.assertEqual(card.bill(6)["subtotal"], 600)
        self.assertEqual(card.bill(6)["total"], 800)
        self.assertEqual(card.bill(20)["total"], 1000)
        self.assertEqual(card.minimum, 800)
        combo = RateCard(FLAT_CARD, minimum=800, cap=1500)
        combo.add_discount("half", KIND_PERCENT, 5000)
        self.assertEqual(combo.bill(10)["total"], 800)
        self.assertGreaterEqual(combo.bill(10)["total"], combo.minimum)
        self.assertGreaterEqual(card.bill(3)["total"], 800)


class CapTest(unittest.TestCase):
    """封顶：折后金额超过封顶的按封顶收，没超过的照实收。"""

    def test_06_cap_limits_the_discounted_total(self):
        card = RateCard(FLAT_CARD, cap=1500)
        card.add_discount("half", KIND_PERCENT, 5000)
        self.assertEqual(card.cap, 1500)
        self.assertEqual(card.bill(10)["total"], 500)
        self.assertEqual(card.bill(20)["total"], 1000)
        self.assertEqual(card.bill(30)["total"], 1500)
        self.assertLessEqual(card.bill(30)["total"], 1500)
        combo = RateCard(FLAT_CARD, minimum=800, cap=1500)
        combo.add_discount("half", KIND_PERCENT, 5000)
        self.assertEqual(combo.bill(20)["total"], 1000)
        self.assertEqual(combo.bill(40)["total"], 1500)
        self.assertLessEqual(combo.bill(40)["total"], 1500)


class StackingTest(unittest.TestCase):
    """折扣叠加：每个折扣作用在上一步的余额上。"""

    def test_07_discounts_stack_on_the_running_balance(self):
        card = RateCard(FLAT_CARD)
        card.add_discount("ten_a", KIND_PERCENT, 1000)
        card.add_discount("ten_b", KIND_PERCENT, 1000)
        bill = card.bill(10)
        self.assertEqual([row["reduction"] for row in bill["discounts"]],
                         [100, 90])
        self.assertEqual(bill["total"], 810)
        self.assertEqual(card.bill(13)["subtotal"], 1300)
        self.assertEqual(card.bill(13)["total"], 1053)
        mixed = RateCard(FLAT_CARD)
        mixed.add_discount("ten", KIND_PERCENT, 1000)
        mixed.add_discount("cut", KIND_FLAT, 50)
        mixed.add_discount("twenty", KIND_PERCENT, 2000)
        mixed_bill = mixed.bill(10)
        self.assertEqual([row["name"] for row in mixed_bill["discounts"]],
                         ["ten", "twenty", "cut"])
        self.assertEqual(mixed_bill["total"], 670)


class OrderTest(unittest.TestCase):
    """生效次序：百分比折扣整体先于定额减免，同类保持登记顺序。"""

    def test_08_percent_discounts_apply_before_flat_ones(self):
        card = RateCard(FLAT_CARD)
        card.add_discount("cut", KIND_FLAT, 300)
        card.add_discount("forty", KIND_PERCENT, 4000)
        bill = card.bill(10)
        self.assertEqual(bill["subtotal"], 1000)
        self.assertEqual(bill["total"], 300)
        self.assertEqual(card.bill(5)["total"], 0)
        self.assertEqual(card.bill(25)["total"], 1200)


class ClampTest(unittest.TestCase):
    """定额减免：最多把余额扣到 0，不把金额扣成负数。"""

    def test_09_flat_discount_never_pushes_the_total_below_zero(self):
        card = RateCard(FLAT_CARD)
        card.add_discount("half", KIND_PERCENT, 5000)
        card.add_discount("big", KIND_FLAT, 800)
        bill = card.bill(10)
        self.assertEqual([row["reduction"] for row in bill["discounts"]],
                         [500, 500])
        self.assertEqual(bill["total"], 0)
        self.assertGreaterEqual(bill["total"], 0)
        small = card.bill(7)
        self.assertEqual([row["reduction"] for row in small["discounts"]],
                         [350, 350])
        self.assertEqual(small["total"], 0)
        self.assertEqual(card.bill(20)["total"], 200)
        self.assertGreaterEqual(card.bill(20)["total"], 0)


class LifecycleTest(unittest.TestCase):
    """登记、只读视图与异常输入。"""

    def test_10_tier_and_discount_validation(self):
        with self.assertRaises(ValueError):
            RateCard([])
        with self.assertRaises(TypeError):
            RateCard(7)
        with self.assertRaises(ValueError):
            RateCard([(100,)])
        with self.assertRaises(ValueError):
            RateCard([(100, 300)])
        with self.assertRaises(ValueError):
            RateCard([(None, 300), (None, 100)])
        with self.assertRaises(ValueError):
            RateCard([(100, 300), (100, 200), (None, 100)])
        with self.assertRaises(ValueError):
            RateCard([(100, 300), (50, 200), (None, 100)])
        with self.assertRaises(ValueError):
            RateCard([(100, -1), (None, 100)])
        with self.assertRaises(TypeError):
            RateCard([(100, 300.0), (None, 100)])
        with self.assertRaises(TypeError):
            RateCard([(True, 300), (None, 100)])
        with self.assertRaises(ValueError):
            RateCard(FLAT_CARD, minimum=-1)
        with self.assertRaises(ValueError):
            RateCard(FLAT_CARD, cap=-1)
        with self.assertRaises(ValueError):
            RateCard(FLAT_CARD, minimum=900, cap=800)
        with self.assertRaises(TypeError):
            RateCard(FLAT_CARD, minimum=1.5)
        card = RateCard([(100, 300), (500, 200), (None, 100)],
                        minimum=50, cap=500000)
        self.assertEqual(card.tiers(), ((100, 300), (500, 200), (None, 100)))
        self.assertEqual(validate_tiers(TIERS), ((100, 300), (500, 200), (None, 100)))
        self.assertEqual(card.minimum, 50)
        self.assertEqual(card.cap, 500000)
        self.assertEqual(card.discounts(), ())
        discount = card.add_discount("promo", KIND_PERCENT, 1250)
        self.assertIsInstance(discount, Discount)
        self.assertEqual((discount.name, discount.kind, discount.value),
                         ("promo", KIND_PERCENT, 1250))
        self.assertEqual(card.discounts(), (discount,))
        with self.assertRaises(ValueError):
            card.add_discount("promo", KIND_FLAT, 100)
        with self.assertRaises(ValueError):
            card.add_discount("", KIND_FLAT, 100)
        with self.assertRaises(ValueError):
            card.add_discount("x", "ratio", 100)
        with self.assertRaises(ValueError):
            card.add_discount("x", KIND_PERCENT, 10001)
        with self.assertRaises(ValueError):
            card.add_discount("x", KIND_FLAT, -1)
        with self.assertRaises(TypeError):
            card.add_discount("x", KIND_FLAT, 1.5)
        with self.assertRaises(ValueError):
            card.bill(-1)
        with self.assertRaises(TypeError):
            card.bill(True)
        with self.assertRaises(TypeError):
            card.bill(2.0)
        with self.assertRaises(ValueError):
            split_usage(-1, FLAT_CARD)
        with self.assertRaises(TypeError):
            split_usage(1.5, FLAT_CARD)
        with self.assertRaises(ValueError):
            round_half_up(-1, 2)
        with self.assertRaises(ValueError):
            round_half_up(1, 0)
        with self.assertRaises(TypeError):
            round_half_up(1, True)
        with self.assertRaises(ValueError):
            segment_amount(-1, 100)
        with self.assertRaises(TypeError):
            segment_amount(1, True)
        self.assertEqual(round_half_up(0, 7), 0)
        self.assertEqual(segment_amount(0, 999), 0)
        self.assertEqual(split_usage(0, [(100, 300), (None, 100)]), [0, 0])
        zero = card.bill(0)
        self.assertEqual([row["units"] for row in zero["segments"]], [0, 0, 0])
        self.assertEqual(zero["subtotal"], 0)
        self.assertEqual(zero["total"], 50)


if __name__ == "__main__":
    unittest.main()
