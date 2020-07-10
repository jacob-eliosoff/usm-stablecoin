import math
import sys

# Parameters:
USM_MINT_FEE    = 0.001
USM_BURN_FEE    = 0.005
FUM_CREATE_FEE  = 0.001
FUM_REDEEM_FEE  = 0.005
MAX_DEBT_RATIO  = 0.8   # Eg, if 1,000,000 USM are outstanding, users won't be able to redeem FUM unless the ETH pool's value is >= $1,000,000 / 0.8 = $1,250,000

# Price side constants:
THEORETICAL = 'theoretical'
BUY         = 'buy'
SELL        = 'sell'

# State:
eth_price                   = 200
pool_eth                    = 0
usm_holdings                = {}
fum_holdings                = {}
min_fum_buy_price_in_eth    = 0

def main():
    input_loop()

def input_loop():
    while True:
        clear_min_fum_buy_price_if_obsolete()
        print(status_summary())
        print()
        line = input("> ")
        words = line.split()
        try:
            if words[0] == "price":
                # "price 150" -> change the current ETH price in our simulation to $150
                new_price = float(words[1])
                change_eth_price(new_price)
            elif words[0] == "mint":
                # "mint A 10" -> user A adds 10 ETH to the pool, getting back 10 * eth_price newly-minted USM (minus fees)
                user, eth_to_add = words[1], float(words[2])
                usm_minted = mint_usm(user, eth_to_add)
                print("Minted {:,} new USM for {} from {:,} ETH.".format(round(usm_minted, 4), user, round(eth_to_add, 6)))
            elif words[0] == "burn":
                # "burn A 1000" -> user A burns 1,000 of their USM, getting back (1,000 / eth_price) ETH (minus fees)
                user, usm_to_burn = words[1], float(words[2])
                eth_removed = burn_usm(user, usm_to_burn)
                print("Burned {:,} of {}'s USM for ${:,} each, yielding {:,} ETH.".format(round(usm_to_burn, 4), user, round(eth_removed * eth_price / usm_to_burn, 6), round(eth_removed, 6)))
            elif words[0] == "fund_eth":
                # "fund_eth B 5" -> user B adds 5 ETH to the pool, getting back a corresponding amount of newly-created FUM (based on the current FUM price, roughly buffer_value() / fum_outstanding())
                user, eth_to_add = words[1], float(words[2])
                fum_created = create_fum_from_eth(user, eth_to_add)
                print("Created {:,} new FUM for {} from {:,} ETH.".format(round(fum_created, 4), user, round(eth_to_add, 6)))
            elif words[0] == "fund_usm":
                # "fund_usm B 1000" -> user B returns (burns) 1000 USM to the pool, getting back a corresponding amount of newly-created FUM (based on the current FUM price).  This leaves total pool value unchanged - basically converting USM to FUM, deceasing debt ratio.
                user, usm_to_convert = words[1], float(words[2])
                fum_created = create_fum_from_usm(user, usm_to_convert)
                print("Created {:,} new FUM for {} from {:,} USM.".format(round(fum_created, 4), user, round(usm_to_convert, 4)))
            elif words[0] == "defund":
                # "defund B 1000" -> user B redeems 1,000 of their FUM, getting back a corresponding amount of ETH (based on the current FUM price)
                user, fum_to_redeem = words[1], float(words[2])
                eth_removed = redeem_fum(user, fum_to_redeem)
                print("Redeemed {:,} of {}'s FUM for ${:,} each, yielding {:,} ETH.".format(round(fum_to_redeem, 4), user, round(eth_removed * eth_price / fum_to_redeem, 6), round(eth_removed, 6)))
            else:
                raise ValueError("Unrecognized command: '{}'".format(words))
        except:
            print("Error:", sys.exc_info())

def status_summary():
    min_fum_buy_price_string = "" if min_fum_buy_price_in_eth == 0 else " (min {:,} ETH = ${:,})".format(round(min_fum_buy_price_in_eth, 6), round(min_fum_buy_price_in_eth * eth_price, 8))
    return "{:,} ETH at ${:,} = ${:,} pool value, {:,} USM outstanding, buffer = ${:,}, debt ratio = {:.2%}, {:,} FUM outstanding, FUM price = ${:,}/${:,}{}\nUSM holdings: {}\nFUM holdings: {}".format(
        round(pool_eth, 6), round(eth_price, 4), round(pool_value(), 2), round(usm_outstanding(), 4), round(buffer_value(), 2), debt_ratio(), round(fum_outstanding(), 4), round(fum_price(SELL), 6), round(fum_price(BUY), 6), min_fum_buy_price_string, usm_holdings, fum_holdings)


# State-modifying operations:

def change_eth_price(new_price):
    global eth_price
    eth_price = new_price
    if min_fum_buy_price_in_eth == 0 and debt_ratio() > MAX_DEBT_RATIO and fum_outstanding() > 0:
        # Need to set the min FUM buy price (in ETH), to the FUM price in ETH as of the ETH price point where the debt ratio exceeded MAX_DEBT_RATIO.  Eg, suppose pool_eth = 400 and fum_outstanding() = 1,000.  Then, at the moment we exceed MAX_DEBT_RATIO = 0.8, the buffer must contain
        # 400 * (1 - 0.8) = 80 ETH, and therefore the FUM price in ETH is 80 / 1,000 = 0.08.  Eg, suppose USM outstanding = 40,000: then we cross 0.8 at ETH = $125, when total pool value = $50,000, buffer = $10,000, and FUM price = $10,000 / 1,000 = $10 = ($10 / $125) = 0.08 ETH.
        fum_price_in_eth_at_which_we_crossed_max_debt_ratio = (pool_eth * (1 - MAX_DEBT_RATIO)) / fum_outstanding()
        set_min_fum_buy_price_in_eth(fum_price_in_eth_at_which_we_crossed_max_debt_ratio / (1 - FUM_CREATE_FEE))    # We want a buy price, so adjust for the fee

def mint_usm(user, eth_to_add):
    global pool_eth
    usm_minted = (eth_to_add * eth_price) * (1 - USM_MINT_FEE)
    pool_eth += eth_to_add
    usm_holdings[user] = usm_holdings.get(user, 0) + usm_minted

    if min_fum_buy_price_in_eth == 0 and debt_ratio() > MAX_DEBT_RATIO and fum_outstanding() > 0:
        # Need to set the min FUM buy price (in ETH), to the FUM price in ETH as of the point during this mint op where the debt ratio exceeded MAX_DEBT_RATIO.  Without fees this would be trivial, since minting affects neither the number of ETH in the buffer, nor the number of FUM
        # outstanding: so we could just divide them!  But, because the fees from minting slightly increase the buffer as we go, the math gets much more hairy...  Just trust me for now (or verify on an example) that this formula gives the correct FUM price in ETH at the crossing point:
        eth_in_buffer = buffer_value() / eth_price
        usm_value_in_eth = usm_outstanding() / eth_price
        fum_price_in_eth_at_which_we_crossed_max_debt_ratio = ((pool_eth +
                                                                (MAX_DEBT_RATIO * eth_in_buffer - (1 - MAX_DEBT_RATIO) * usm_value_in_eth) / (1 - MAX_DEBT_RATIO - USM_MINT_FEE))
                                                               * (1 - MAX_DEBT_RATIO) / fum_outstanding())
        set_min_fum_buy_price_in_eth(fum_price_in_eth_at_which_we_crossed_max_debt_ratio / (1 - FUM_CREATE_FEE))    # We want a buy price, so adjust for the fee
        # Note that minting never pulls us *below* MAX_DEBT_RATIO, since it always moves debt ratio closer to 1.  If debt ratio starts > 1, it stays > 1; if it starts < 1 and > MAX_DEBT_RATIO, it stays > MAX_DEBT_RATIO.

    return usm_minted

def burn_usm(user, usm_to_burn, burn_fee=USM_BURN_FEE, check_debt_ratio=True):
    # Note that burning never pushes us over MAX_DEBT_RATIO (which is < 1), since it always moves debt ratio further from 1.  It can pull us *below* MAX_DEBT_RATIO, but if so the top-level call to clear_min_fum_buy_price_if_obsolete() will take care of it once this op is done.
    global pool_eth
    assert usm_to_burn <= usm_holdings.get(user, 0), "{} doesn't own that many USM".format(user)
    eth_removed = (usm_to_burn / eth_price) * (1 - burn_fee)
    assert eth_removed <= pool_eth, "Not enough ETH in the pool"
    if check_debt_ratio:
        assert debt_ratio(pool_eth - eth_removed, usm_outstanding() - usm_to_burn) <= 1, "Burning {:,} USM would leave the debt ratio above 100%".format(usm_to_burn)
    usm_holdings[user] -= usm_to_burn
    pool_eth -= eth_removed
    return eth_removed

def create_fum_from_eth(user, eth_to_add):
    # Fund operations never push us over MAX_DEBT_RATIO either - they reduce debt ratio.  However, they *can* bring us back under MAX_DEBT_RATIO, so we have to handle that case here.
    global pool_eth
    if debt_ratio() > MAX_DEBT_RATIO:
        eth_add_that_would_bring_us_to_max_dr = (usm_outstanding() / eth_price) / MAX_DEBT_RATIO - pool_eth
        eth_to_add_above_max_dr = max(0, min(eth_to_add, eth_add_that_would_bring_us_to_max_dr))
        fum_created_above_max_dr = (eth_to_add_above_max_dr * eth_price) / fum_price(BUY)
        pool_eth += eth_to_add_above_max_dr
        fum_holdings[user] = fum_holdings.get(user, 0) + fum_created_above_max_dr

        eth_to_add -= eth_to_add_above_max_dr
        if eth_to_add > 0:
            clear_min_fum_buy_price_if_obsolete(True)                   # eth_to_add was enough to bring us back below MAX_DEBT_RATIO (ie, exceeds eth_add_that_would_bring_us_to_max_dr), so we need to clear the min FUM buy price before processing the remaining eth_to_add
    else:
        fum_created_above_max_dr = 0

    fum_created_below_max_dr = (eth_to_add * eth_price) / fum_price(BUY)
    pool_eth += eth_to_add
    fum_holdings[user] = fum_holdings.get(user, 0) + fum_created_below_max_dr
    if debt_ratio() > MAX_DEBT_RATIO and min_fum_buy_price_in_eth == 0:
        set_min_fum_buy_price_in_eth(fum_price(BUY) / eth_price)        # We need this for the particular case where debt ratio was already > max, but we had no FUM outstanding yet until this fund operation
    return fum_created_above_max_dr + fum_created_below_max_dr

def create_fum_from_usm(user, usm_to_convert):
    # To avoid duplication, just implement this as a call to burn_usm() (with 0 fee, and bypassing the debt ratio check), followed by a call to create_fum_from_eth().  This is a bit hazardous because we could die on an error with half the operation complete, but good enough for govt work:
    eth_converted = burn_usm(user, usm_to_convert, 0, False)
    clear_min_fum_buy_price_if_obsolete()                               # Important so that, if the preceding burn brought us below MAX_DEBT_RATIO, we clear the min FUM buy price before starting the fund operation
    return create_fum_from_eth(user, eth_converted)

def redeem_fum(user, fum_to_redeem):
    global pool_eth
    assert fum_to_redeem <= fum_holdings.get(user, 0), "{} doesn't own that many FUM".format(user)
    eth_removed = (fum_to_redeem * fum_price(SELL)) / eth_price
    assert debt_ratio(pool_eth - eth_removed) <= MAX_DEBT_RATIO, "Redeeming {:,} FUM would leave the debt ratio above {:.0%}".format(fum_to_redeem, MAX_DEBT_RATIO)
    # Since we've disallowed redeem operations that would push us over MAX_DEBT_RATIO, we don't need to handle that case.  And a redeem can never pull us under MAX_DEBT_RATIO either, because it increases debt ratio.
    fum_holdings[user] -= fum_to_redeem
    pool_eth -= eth_removed
    return eth_removed

def set_min_fum_buy_price_in_eth(price_in_eth):
    global min_fum_buy_price_in_eth
    print("* Setting min FUM buy price to ${:,} = {:,} ETH, since debt ratio {:.2%} has risen above {:.0%}.".format(round(price_in_eth * eth_price, 6), round(price_in_eth, 8), debt_ratio(), MAX_DEBT_RATIO))
    min_fum_buy_price_in_eth = price_in_eth

def clear_min_fum_buy_price_if_obsolete(bypass_debt_ratio_check=False):
    global min_fum_buy_price_in_eth
    if min_fum_buy_price_in_eth != 0 and (bypass_debt_ratio_check or debt_ratio() <= MAX_DEBT_RATIO):
        print("* Resetting min FUM buy price to $0, since debt ratio {:.2%} is back below {:.0%}.".format(debt_ratio(), MAX_DEBT_RATIO))
        min_fum_buy_price_in_eth = 0


# Informational utility functions:

def usm_outstanding():
    return sum(usm_holdings.values())

def fum_outstanding():
    return sum(fum_holdings.values())

def pool_value(eth=None):
    if eth is None:
        eth = pool_eth
    return eth * eth_price

def buffer_value():
    return pool_value() - usm_outstanding()

def debt_ratio(eth=None, usm=None):
    if eth is None:
        eth = pool_eth
    if usm is None:
        usm = usm_outstanding()
    if pool_value(eth) == 0:
        return 0
    else:
        return usm / pool_value(eth)

def fum_price(side):
    if fum_outstanding() == 0:
        if side == BUY:
            return 1        # Pricing our first FUM purchase, so just price them at $1
        else:
            return math.nan
    else:
        price = buffer_value() / fum_outstanding()
        if side == BUY:
            return max(price / (1 - FUM_CREATE_FEE), min_fum_buy_price_in_eth * eth_price)
        elif side == SELL:
            return max(price * (1 - FUM_REDEEM_FEE), 0)
        else:
            return price

main()
