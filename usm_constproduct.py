from datetime import datetime, timezone
import math
import sys
import traceback

# Parameters:
MAX_DEBT_RATIO                      = 0.8           # Eg, if 1,000,000 USM are outstanding, users won't be able to redeem FUM unless the ETH pool's (mid) value is >= $1,000,000 / 0.8 = $1,250,000
BUY_SELL_ADJUSTMENTS_HALF_LIFE      = 60            # Decay rate of our bid/ask related to recent buy/sell activity (eg, rate of buy price, pushed up by buys, dropping back towards oracle buy price): 1.5 -> 1.2247 -> 1.1067
MIN_FUM_BUY_PRICE_HALF_LIFE         = 24 * 60 * 60  # min_fum_buy_price_in_eth() drops by 50% every day

# Price side constants:
MID                                 = 'mid'
BUY                                 = 'buy'
SELL                                = 'sell'

# State:
time                                = datetime(2020, 8, 1, tzinfo=timezone.utc).timestamp()
oracle_eth_buy_price                = 202
oracle_eth_sell_price               = 198
pool_eth                            = 0
usm_holdings                        = {}
fum_holdings                        = {}
mint_burn_adjustment_stored         = 1         # Price multiplier based on recent mint/burn activity.  Eg, if A just did mint ops driving the ETH sell price down by 0.7x, and B just burned pushing the ETH buy price up by 1.2x, this factor will be 0.84.  Decays towards 1 over time.
mint_burn_adjustment_timestamp      = 0
fund_defund_adjustment_stored       = 1         # Same as above, but for funds (increases factor)/defunds (decreases factor).
fund_defund_adjustment_timestamp    = 0
min_fum_buy_price_in_eth_stored     = 0         # Note that this price is in terms of ETH, not USD/USM.
min_fum_buy_price_timestamp         = 0


# ________________________________________ Main loop ________________________________________

def main():
    input_loop()

def input_loop():
    while True:
        set_min_fum_buy_price_in_eth_if_needed()    # The price calculation here technically may not quite right, because the theoretical FUM price increases (slightly) *during* many ops, as we collect fees...  But #letskeepitsimple
        clear_min_fum_buy_price_if_obsolete()
        print(status_summary())
        print()
        line = input("> ")
        words = line.split()
        try:
            if words[0] == "price":
                # "price 150" -> change the current ETH price (buy and sell) in our simulation to $150, or "price 150/160" -> sell price $150, buy price $160
                prices = map(float, words[1].split('/'))
                set_oracle_eth_price(*prices)
            elif words[0] == "mint":
                # "mint A 10" -> user A adds 10 ETH to the pool, getting back 10 * eth_sell_price newly-minted USM (minus fees)
                user, eth_to_add = words[1], float(words[2])
                usm_minted = mint_usm(user, eth_to_add)
                print("Minted {:,} new USM for {} from {:,} ETH, for {:,} ETH (~${:,}) each.".format(round(usm_minted, 4), user, round(eth_to_add, 6), round(eth_to_add / usm_minted, 8), round(eth_to_add / usm_minted * calc_eth_price(MID), 6)))
            elif words[0] == "burn":
                # "burn A 1000" -> user A burns 1,000 of their USM, getting back (1,000 / eth_buy_price) ETH (minus fees)
                user, usm_to_burn = words[1], float(words[2])
                eth_removed = burn_usm(user, usm_to_burn)
                print("Burned {:,} of {}'s USM for {:,} ETH (~${:,}) each, yielding {:,} ETH.".format(round(usm_to_burn, 4), user, round(eth_removed / usm_to_burn, 8), round(eth_removed / usm_to_burn * calc_eth_price(MID), 6), round(eth_removed, 6)))
            elif words[0] == "fund_eth":
                # "fund_eth B 5" -> user B adds 5 ETH to the pool, getting back a corresponding amount of newly-created FUM (based on the current FUM price, roughly buffer_value() / fum_outstanding())
                user, eth_to_add = words[1], float(words[2])
                fum_created = create_fum_from_eth(user, eth_to_add)
                print("Created {:,} new FUM for {} from {:,} ETH, for {:,} ETH (~${:,}) each.".format(round(fum_created, 4), user, round(eth_to_add, 6), round(eth_to_add / fum_created, 8), round(eth_to_add / fum_created * calc_eth_price(MID), 6)))
            elif words[0] == "fund_usm":
                # "fund_usm B 1000" -> user B returns (burns) 1000 USM to the pool, getting back a corresponding amount of newly-created FUM (based on the current FUM price).  This leaves total pool value unchanged - basically converting USM to FUM, deceasing debt ratio.
                user, usm_to_convert = words[1], float(words[2])
                fum_created = create_fum_from_usm(user, usm_to_convert)
                print("Created {:,} new FUM for {} from {:,} USM, for {:,} USM (~${:,}) each.".format(round(fum_created, 4), user, round(usm_to_convert, 4), round(usm_to_convert / fum_created, 6), round(usm_to_convert / fum_created, 6)))
            elif words[0] == "defund":
                # "defund B 1000" -> user B redeems 1,000 of their FUM, getting back a corresponding amount of ETH (based on the current FUM price)
                user, fum_to_redeem = words[1], float(words[2])
                eth_removed = redeem_fum(user, fum_to_redeem)
                print("Redeemed {:,} of {}'s FUM for {:,} ETH (~${:,}) each, yielding {:,} ETH.".format(round(fum_to_redeem, 4), user, round(eth_removed / fum_to_redeem, 8), round(eth_removed / fum_to_redeem * calc_eth_price(MID), 6), round(eth_removed, 6)))
            elif words[0] == "wait":
                # "wait 300" -> wait 300 seconds (5 minutes)
                wait = float(words[1])
                set_time(time + wait)
            else:
                raise ValueError("Unrecognized command: '{}'".format(words))
        except Exception as err:
            print("Error:", sys.exc_info())
            traceback.print_tb(err.__traceback__)

def status_summary():
    time_string = datetime.utcfromtimestamp(time).strftime('%Y/%m/%d %H:%M:%S')
    min_fum_buy_price_string = "" if min_fum_buy_price_in_eth() == 0 else ", min {:,} ETH (~${:,})".format(round(min_fum_buy_price_in_eth(), 8), round(min_fum_buy_price_in_eth() * calc_eth_price(MID), 6))
    return "{}: {:,} ETH at ${:,} (${:,}/${:,}) = ${:,} pool value, {:,} USM outstanding (${:,}/${:,}, adj {}), buffer = ${:,}, debt ratio = {:.2%}, {:,} FUM outstanding (${:,}/${:,}{}, adj {})\nUSM holdings: {}\nFUM holdings: {}".format(
        time_string, round(pool_eth, 6), round(calc_eth_price(MID), 4), round(oracle_eth_sell_price, 4), round(oracle_eth_buy_price, 4), round(pool_value(), 2), round(usm_outstanding(), 4), round(calc_usm_price(SELL), 6), round(calc_usm_price(BUY), 6), round(mint_burn_adjustment(), 6),
        round(buffer_value(), 2), debt_ratio(), round(fum_outstanding(), 4), round(calc_fum_price(SELL), 6), round(calc_fum_price(BUY), 6), min_fum_buy_price_string, round(fund_defund_adjustment(), 6), usm_holdings, fum_holdings)


# ________________________________________ State-modifying operations ________________________________________

def set_time(new_time):
    global time
    time = new_time

def set_oracle_eth_price(new_price, new_buy_price=None):
    global oracle_eth_sell_price, oracle_eth_buy_price
    oracle_eth_sell_price = new_price
    oracle_eth_buy_price = new_buy_price if new_buy_price is not None else new_price

    if min_fum_buy_price_needs_setting():
        # Set the min FUM buy price (in ETH), to the FUM price in ETH as of the ETH price point where the debt ratio exceeded MAX_DEBT_RATIO.  Eg, suppose pool_eth = 400 and fum_outstanding() = 1,000.  Then, at the moment we exceed MAX_DEBT_RATIO = 0.8, the buffer must contain
        # 400 * (1 - 0.8) = 80 ETH, and therefore the FUM price in ETH is 80 / 1,000 = 0.08.  Eg, suppose USM outstanding = 40,000: then we cross 0.8 at ETH = $125, when total pool value = $50,000, buffer = $10,000, and FUM price = $10,000 / 1,000 = $10 = ($10 / $125) = 0.08 ETH.
        fum_price_in_eth_at_which_we_crossed_max_debt_ratio = (pool_eth * (1 - MAX_DEBT_RATIO)) / fum_outstanding()
        set_min_fum_buy_price_in_eth_if_needed(fum_price_in_eth_at_which_we_crossed_max_debt_ratio)

def mint_usm(user, eth_to_add):
    # Note that minting never pulls us *below* MAX_DEBT_RATIO, since it always moves debt ratio closer to 1.  If debt ratio starts > 1, it stays > 1; if it starts < 1 and > MAX_DEBT_RATIO, it stays > MAX_DEBT_RATIO.
    global pool_eth
    initial_eth_price = calc_eth_price(SELL)
    if pool_eth == 0:
        # This is our very first ETH in the pool, so need to special-case it (otherwise the division below blows up):
        usm_minted = eth_to_add * initial_eth_price
    else:
        # Mint at a sliding-down ETH price (ie, buy USM at a sliding-up USM price):
        pool_eth_growth_factor = (pool_eth + eth_to_add) / pool_eth
        usm_minted = pool_eth * initial_eth_price * math.log(pool_eth_growth_factor)                                # Math: this is an integral - sum of all USM minted at a sliding-down ETH price
        set_mint_burn_adjustment(mint_burn_adjustment() / pool_eth_growth_factor)
    pool_eth += eth_to_add
    usm_holdings[user] = usm_holdings.get(user, 0) + usm_minted
    return usm_minted

def burn_usm(user, usm_to_burn, check_debt_ratio=True):
    # Note that burning never pushes us over MAX_DEBT_RATIO (which is < 1), since it always moves debt ratio further from 1.  It can pull us *below* MAX_DEBT_RATIO, but if so the top-level call to clear_min_fum_buy_price_if_obsolete() will take care of it once this op is done.
    global pool_eth
    assert usm_to_burn <= usm_holdings.get(user, 0), "{} doesn't own that many USM".format(user)
    initial_eth_price = calc_eth_price(BUY)
    # Burn at a sliding-up price:
    eth_removed = pool_eth * (1 - math.exp(-usm_to_burn / (pool_eth * initial_eth_price)))                          # Math: this is an integral - sum of all USM burned at a sliding price, must match usm_burned_at_sliding_price above
    assert eth_removed <= pool_eth, "Not enough ETH in the pool"
    if check_debt_ratio:
        assert debt_ratio(eth=pool_eth - eth_removed, usm=usm_outstanding() - usm_to_burn) <= 1, "Burning {:,} USM would leave the debt ratio above 100%".format(usm_to_burn)   # Note: the risk is not this burn op pushing us over 100%, but that a previous price drop might have done so!
    pool_eth_shrink_factor = (pool_eth - eth_removed) / pool_eth
    set_mint_burn_adjustment(mint_burn_adjustment() / pool_eth_shrink_factor)
    usm_holdings[user] -= usm_to_burn
    pool_eth -= eth_removed
    return eth_removed

def create_fum_from_eth(user, eth_to_add):
    # Fund operations never push us over MAX_DEBT_RATIO: they reduce debt ratio.  However, they *can* bring us back *under* MAX_DEBT_RATIO, which means the naive logic here may overcharge an op that pulls debt ratio below MAX_DEBT_RATIO midway through...  But oh well, #letskeepitsimple
    global pool_eth
    if fum_outstanding() == 0:
        # This is our very first FUM created, so need to special-case it (otherwise the division below blows up):
        fum_created = eth_to_add * calc_eth_price(MID)                                                              # No need for any adjustment: the FUM price only matters relative to an existing FUM price, so we can just price the first FUM units at $1
    else:
        # Create at a sliding-up price:
        initial_fum_price = calc_fum_price(BUY)
        initial_eth_price_in_fum = calc_eth_price(MID) / initial_fum_price                                          # Don't apply adjustment to the ETH price - the adjustment should only be applied as the last step in a transaction, not when used indirectly for pricing as here.
        pool_eth_growth_factor = (pool_eth + eth_to_add) / pool_eth
        fum_created = pool_eth * initial_eth_price_in_fum * math.log(pool_eth_growth_factor)                        # Math: this is an integral - sum of all FUM created at a sliding-up FUM price
        set_fund_defund_adjustment(fund_defund_adjustment() * pool_eth_growth_factor)
    pool_eth += eth_to_add
    fum_holdings[user] = fum_holdings.get(user, 0) + fum_created
    return fum_created

def create_fum_from_usm(user, usm_to_convert):
    # To avoid duplication, just implement this as a call to burn_usm() (bypassing the debt ratio check), followed by a call to create_fum_from_eth().  This is a bit hazardous because we could die on an error with half the operation complete, but good enough for govt work:
    eth_converted = burn_usm(user, usm_to_convert, check_debt_ratio=False)
    clear_min_fum_buy_price_if_obsolete()                                                                           # Important so that, if the preceding burn brought us below MAX_DEBT_RATIO, we clear the min FUM buy price before starting the fund operation
    return create_fum_from_eth(user, eth_converted)

def redeem_fum(user, fum_to_redeem):
    global pool_eth
    assert fum_to_redeem <= fum_holdings.get(user, 0), "{} doesn't own that many FUM".format(user)
    initial_fum_price = calc_fum_price(SELL)
    initial_eth_price_in_fum = calc_eth_price(MID) / initial_fum_price                                              # Don't apply adjustment to the ETH price - see similar comment above.
    eth_removed = pool_eth * (1 - math.exp(-fum_to_redeem / (pool_eth * initial_eth_price_in_fum)))                 # Math: see closely analogous comment in burn_usm() above
    assert debt_ratio(eth=pool_eth - eth_removed) <= MAX_DEBT_RATIO, "Redeeming {:,} FUM would leave the debt ratio above {:.0%}".format(fum_to_redeem, MAX_DEBT_RATIO)
    # Since we've disallowed redeem operations that would push us over MAX_DEBT_RATIO, we don't need to handle that case.  And a redeem can never pull us under MAX_DEBT_RATIO either, because it increases debt ratio.
    pool_eth_shrink_factor = (pool_eth - eth_removed) / pool_eth
    set_fund_defund_adjustment(fund_defund_adjustment() * pool_eth_shrink_factor)
    fum_holdings[user] -= fum_to_redeem
    pool_eth -= eth_removed
    return eth_removed

def set_min_fum_buy_price_in_eth_if_needed(price_in_eth=None):
    global min_fum_buy_price_in_eth_stored, min_fum_buy_price_timestamp
    if min_fum_buy_price_needs_setting():
        eth_price = calc_eth_price(MID)
        if price_in_eth is None:
            # If no price is passed in, set it to the current theoretical FUM price (without adjustments), in ETH:
            price_in_usd = calc_fum_price(BUY, adjusted=False, mfbp=False)
            price_in_eth = price_in_usd / eth_price
        else:
            price_in_usd = price_in_eth * eth_price
        print("* Setting min FUM buy price to {:,} ETH (~${:,}), since debt ratio {:.2%} is above {:.0%}.".format(round(price_in_eth, 8), round(price_in_usd, 6), debt_ratio(), MAX_DEBT_RATIO))
        min_fum_buy_price_in_eth_stored = price_in_eth
        min_fum_buy_price_timestamp = time

def clear_min_fum_buy_price_if_obsolete():
    global min_fum_buy_price_in_eth_stored, min_fum_buy_price_timestamp
    if min_fum_buy_price_in_eth() != 0 and debt_ratio() <= MAX_DEBT_RATIO:
        print("* Resetting min FUM buy price to $0, since debt ratio {:.2%} is back below {:.0%}.".format(debt_ratio(), MAX_DEBT_RATIO))
        min_fum_buy_price_in_eth_stored = 0
        min_fum_buy_price_timestamp = None

def set_mint_burn_adjustment(adjustment_factor):
    global mint_burn_adjustment_stored, mint_burn_adjustment_timestamp
    mint_burn_adjustment_stored = adjustment_factor
    mint_burn_adjustment_timestamp = time

def set_fund_defund_adjustment(adjustment_factor):
    global fund_defund_adjustment_stored, fund_defund_adjustment_timestamp
    fund_defund_adjustment_stored = adjustment_factor
    fund_defund_adjustment_timestamp = time


# ________________________________________ Informational utility functions ________________________________________

def usm_outstanding():
    return sum(usm_holdings.values())

def fum_outstanding():
    return sum(fum_holdings.values())

def pool_value(eth=None, eth_price=None):
    if eth is None:
        eth = pool_eth
    if eth_price is None:
        eth_price = calc_eth_price(MID)
    return eth * eth_price

def buffer_value(eth_price=None):
    if eth_price is None:
        eth_price = calc_eth_price(MID)
    return pool_value(eth_price=eth_price) - usm_outstanding()

def debt_ratio(eth=None, usm=None):
    if eth is None:
        eth = pool_eth
    if usm is None:
        usm = usm_outstanding()
    if pool_value(eth=eth) == 0:
        return 0
    else:
        return usm / pool_value(eth=eth)

def calc_eth_price(side, adjusted=True):
    assert side in (MID, BUY, SELL)
    if side == BUY:
        price = oracle_eth_buy_price
        if adjusted:
            price *= max(1, mint_burn_adjustment()) * max(1, fund_defund_adjustment())
    elif side == SELL:
        price = oracle_eth_sell_price
        if adjusted:
            price *= min(1, mint_burn_adjustment()) * min(1, fund_defund_adjustment())
    else:
        price = (oracle_eth_sell_price + oracle_eth_buy_price) / 2
    return price

def calc_fum_price(side, adjusted=True, mfbp=True):
    assert side in (MID, BUY, SELL)
    if fum_outstanding() == 0:
        return 1 if side == BUY else math.nan                                       # If we're pricing our first FUM purchase, just price them at $1, skipping adjustment
    else:
        eth_price = calc_eth_price(side, adjusted=False)                            # adjusted=False because we apply that directly to the FUM price below, not to the ETH price used to calculate the buffer value - that would exaggerate the fee too much
        price = buffer_value(eth_price=eth_price) / fum_outstanding()

    if side == BUY:
        if adjusted:
            price *= max(1, mint_burn_adjustment()) * max(1, fund_defund_adjustment())
        if mfbp:
            price = max(price, min_fum_buy_price_in_eth() * calc_eth_price(MID))
    elif side == SELL:
        if adjusted:
            price *= min(1, mint_burn_adjustment()) * min(1, fund_defund_adjustment())
    return price

def calc_usm_price(side, adjusted=True):
    assert side in (MID, BUY, SELL)
    eth_side = {BUY: SELL, SELL: BUY, MID: MID}[side]
    return calc_eth_price(MID) / calc_eth_price(eth_side, adjusted=adjusted)

def min_fum_buy_price_needs_setting():
    return min_fum_buy_price_in_eth() == 0 and debt_ratio() > MAX_DEBT_RATIO and fum_outstanding() > 0

def min_fum_buy_price_in_eth():
    if min_fum_buy_price_timestamp is None:
        return 0
    else:
        return min_fum_buy_price_in_eth_stored * (0.5 ** ((time - min_fum_buy_price_timestamp) / MIN_FUM_BUY_PRICE_HALF_LIFE))

def mint_burn_adjustment():
    return mint_burn_adjustment_stored ** (0.5 ** ((time - mint_burn_adjustment_timestamp) / BUY_SELL_ADJUSTMENTS_HALF_LIFE))

def fund_defund_adjustment():
    return fund_defund_adjustment_stored ** (0.5 ** ((time - fund_defund_adjustment_timestamp) / BUY_SELL_ADJUSTMENTS_HALF_LIFE))


main()
