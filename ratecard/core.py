"""ratecard：阶梯计费内核（纯标准库，行为完全确定）。

一次计费分四步：把用量按阶梯区间切成若干段，逐段按单价算出金额，按次序
叠加折扣，最后套用最低消费与封顶。对外接口：round_half_up 做分位舍入，
validate_tiers 检查阶梯，split_usage 切分用量，segment_amount 算单段金额，
Discount 是一条折扣，RateCard 把四步串起来交出账单。

约定：

* 金额一律是整数分，单价是百分之一分的整数（PRICE_SCALE = 100）；
* 用量是非负整数；阶梯区间左闭右开，第一档吃掉前 limit 个单位，恰好落在
  档位边界上的用量只按前一段计，各段用量之和恰好等于总用量；
* 每段金额各自四舍五入到分、半数进位，跨段总价等于各段金额之和；
* 折扣分百分比（万分之几的整数）与定额（分）两种：百分比折扣先按登记顺序
  依次作用在余额上，再按登记顺序依次扣减定额减免，定额最多把余额扣到 0，
  任何折扣都不会让金额变成负数；
* 折后金额不足最低消费的按最低消费收，超过封顶的按封顶收；最低消费不能
  高于封顶。

内核不读时钟、不做 I/O、不起线程、不用随机数，同一串调用必然同一结果。
"""

PRICE_SCALE = 100

PERCENT_SCALE = 10000

KIND_PERCENT = "percent"

KIND_FLAT = "flat"


def require_int(value, label):
    """确认参数是非布尔的整数。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("%s 必须是整数: %r" % (label, value))
    return value


def round_half_up(numerator, denominator):
    """把分数四舍五入到整数，半数进位。"""
    require_int(numerator, "分子")
    require_int(denominator, "分母")
    if numerator < 0:
        raise ValueError("分子不能为负: %r" % (numerator,))
    if denominator <= 0:
        raise ValueError("分母必须为正: %r" % (denominator,))
    return (numerator + denominator // 2) // denominator


def validate_tiers(tiers):
    """检查阶梯，返回 (上限, 单价) 元组；最后一档的上限必须是空。"""
    if not isinstance(tiers, (list, tuple)):
        raise TypeError("阶梯必须是序列: %r" % (tiers,))
    if not tiers:
        raise ValueError("阶梯不能为空")
    checked = []
    previous = 0
    last = len(tiers) - 1
    for position, item in enumerate(tiers):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("每个阶梯必须是 (上限, 单价) 二元组: %r" % (item,))
        limit, price = item
        require_int(price, "单价")
        if price < 0:
            raise ValueError("单价不能为负: %r" % (price,))
        if limit is None:
            if position != last:
                raise ValueError("只有最后一档的上限可以为空: %r" % (position,))
        else:
            require_int(limit, "档位上界")
            if limit <= previous:
                raise ValueError("档位上界必须严格递增: %r" % (limit,))
            previous = limit
        checked.append((limit, price))
    if checked[last][0] is not None:
        raise ValueError("最后一档的上限必须为空: %r" % (checked[last][0],))
    return tuple(checked)


def split_usage(usage, tiers):
    """把用量按阶梯区间切分，返回每一档吃到的用量。

    区间左闭右开：第 n 档吃到上一档上限之后到本档上限为止的单位，恰好等于
    某档上限的用量整份归那一档，各档用量之和等于总用量。
    """
    require_int(usage, "用量")
    if usage < 0:
        raise ValueError("用量不能为负: %r" % (usage,))
    checked = validate_tiers(tiers)
    units = []
    previous = 0          # 已切走的单位数；第一档从第 1 个单位吃到本档上限
    remaining = usage
    for limit, _price in checked:
        if limit is None:
            taken = remaining
        else:
            width = limit - previous
            taken = remaining if remaining < width else width
        if taken < 0:
            taken = 0
        units.append(taken)
        remaining -= taken
        if limit is not None:
            previous = limit
    return units


def segment_amount(units, unit_price):
    """一段用量的金额：用量乘单价后四舍五入到分，半数进位。"""
    require_int(units, "用量")
    require_int(unit_price, "单价")
    if units < 0:
        raise ValueError("用量不能为负: %r" % (units,))
    if unit_price < 0:
        raise ValueError("单价不能为负: %r" % (unit_price,))
    return round_half_up(units * unit_price, PRICE_SCALE)


class Discount:
    """一条折扣：名字、类型（percent 或 flat）与整数数值。"""

    __slots__ = ("name", "kind", "value")

    def __init__(self, name, kind, value):
        if not isinstance(name, str) or not name:
            raise ValueError("折扣名字必须是非空字符串: %r" % (name,))
        if kind not in (KIND_PERCENT, KIND_FLAT):
            raise ValueError("折扣类型必须是 percent 或 flat: %r" % (kind,))
        require_int(value, "折扣值")
        if value < 0:
            raise ValueError("折扣值不能为负: %r" % (value,))
        if kind == KIND_PERCENT and value > PERCENT_SCALE:
            raise ValueError("百分比折扣不能超过一份: %r" % (value,))
        self.name = name
        self.kind = kind
        self.value = value


class RateCard:
    """一张价目表：阶梯单价、折扣、最低消费与封顶。"""

    __slots__ = ("_tiers", "_minimum", "_cap", "_discounts")

    def __init__(self, tiers, minimum=0, cap=None):
        self._tiers = validate_tiers(tiers)
        require_int(minimum, "最低消费")
        if minimum < 0:
            raise ValueError("最低消费不能为负: %r" % (minimum,))
        if cap is not None:
            require_int(cap, "封顶")
            if cap < 0:
                raise ValueError("封顶不能为负: %r" % (cap,))
            if minimum > cap:
                raise ValueError("最低消费不能高于封顶: %r > %r" % (minimum, cap))
        self._minimum = minimum
        self._cap = cap
        self._discounts = []

    @property
    def minimum(self):
        """最低消费（分）。"""
        return self._minimum

    @property
    def cap(self):
        """封顶（分）；没有封顶时为 None。"""
        return self._cap

    def tiers(self):
        """按登记顺序返回阶梯。"""
        return self._tiers

    def discounts(self):
        """按登记顺序返回折扣。"""
        return tuple(self._discounts)

    def add_discount(self, name, kind, value):
        """登记一条折扣；同名折扣只允许登记一次。"""
        for existing in self._discounts:
            if existing.name == name:
                raise ValueError("折扣已经登记过: %r" % (name,))
        discount = Discount(name, kind, value)
        self._discounts.append(discount)
        return discount

    def bill(self, usage):
        """按用量出账：分段、逐段计价、叠折扣、垫最低消费与压封顶。"""
        require_int(usage, "用量")
        if usage < 0:
            raise ValueError("用量不能为负: %r" % (usage,))
        units = split_usage(usage, self._tiers)
        segments = []
        subtotal = 0
        for index, ((_limit, price), count) in enumerate(zip(self._tiers, units)):
            amount = segment_amount(count, price)
            subtotal += amount
            segments.append({
                "index": index,
                "units": count,
                "unit_price": price,
                "amount": amount,
            })
        balance, ledger = self._discount_balance(subtotal)
        return {
            "usage": usage,
            "segments": segments,
            "subtotal": subtotal,
            "discounts": ledger,
            "total": self._floor_and_cap(balance),
        }

    def _order_discounts(self):
        """折扣的生效次序：百分比折扣先（保持登记顺序），定额减免后。"""
        percents = [d for d in self._discounts if d.kind == KIND_PERCENT]
        flats = [d for d in self._discounts if d.kind == KIND_FLAT]
        return percents + flats

    def _discount_balance(self, subtotal):
        """按生效次序叠加折扣，返回折后余额与折扣明细。"""
        balance = subtotal
        ledger = []
        for discount in self._order_discounts():
            if discount.kind == KIND_PERCENT:
                reduction = round_half_up(balance * discount.value, PERCENT_SCALE)
            else:
                reduction = min(discount.value, balance)
            balance -= reduction
            ledger.append({
                "name": discount.name,
                "kind": discount.kind,
                "value": discount.value,
                "reduction": reduction,
            })
        return balance, ledger

    def _floor_and_cap(self, balance):
        """折后余额先垫到最低消费，再压到封顶。"""
        total = balance
        if total < self._minimum:
            total = self._minimum
        if self._cap is not None and total > self._cap:
            total = self._cap
        return total
