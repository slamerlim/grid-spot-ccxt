#include "portfolio.hpp"
#include "history.hpp"

#include <iostream>
#include <string_view>

namespace {
using grid::Rational;
unsigned checks = 0;
Rational q(const char* value) { return Rational(value); }
void check(bool condition, const char* detail) { ++checks; if (!condition) throw grid::Error(detail); }
template <typename Function> void rejects(Function function, const char* detail, std::string_view expected = {}) {
    bool rejected = false;
    try { function(); } catch (const grid::Error& error) { rejected = expected.empty() || expected == error.what(); }
    check(rejected, detail);
}
grid::EngineContext context() {
    grid::EngineContext c;
    c.market = {q(".01"), q(".001"), {q(".01"), q("1000000")},
                {q(".001"), std::nullopt}, {q("5"), std::nullopt},
                {q(".001"), std::nullopt}, {q("5"), std::nullopt}};
    c.policy.spacing = q(".01"); c.policy.net_target = q(".01");
    c.policy.quote_reserve = q("1"); c.policy.max_quote_committed = q("900");
    c.policy.lower_price = q(".01"); c.policy.upper_price = q("1000000");
    c.policy.cost_quantum = q("1e-12"); c.policy.max_spread = q(".1");
    c.buy_fee = {"QUOTE", q("0"), q(".00001"), std::nullopt, 1}; c.sell_fee = c.buy_fee;
    c.book = grid::Book{q("11"), q("11.01"), 0, 1}; return c;
}
grid::EngineState state(const grid::EngineContext& c, const char* cash = "0", const char* owned = "10", const char* venue = "okx", const char* symbol = "BTC/USDT") {
    grid::EngineState s; s.scope = {venue, "research", symbol, symbol, "grid", "cycle-1"};
    s.quote_cash = q(cash).exact_units(c.quote_quantum);
    if (q(owned).sign() > 0) {
        s.lots.push_back({99, q(owned).exact_units(c.base_quantum), q("10")});
        s.position_cost = (q(owned) * q("10")).exact_units(c.quote_quantum); s.phase = "accumulating";
    }
    return s;
}
void stamp(grid::EngineContext& c, std::int64_t ms, const char* price = "11") {
    c.now_ms = c.rules_observed_ms = c.fees_observed_ms = ms;
    c.book = grid::Book{q(price), q(price) + q(".01"), ms, c.generation};
}
grid::PortfolioAllocation row(const grid::EngineState& s, const grid::EngineContext& c, const char* price = "11") {
    return {&s, &c, q(price), c.now_ms};
}
void accept(grid::EngineState& s, grid::PortfolioStep& result, std::size_t index = 0) {
    for (auto& action : result.actions) if (action.allocation == index) s = std::move(action.transition.state);
}
void public_roster_guards() {
    auto c = context();
    const auto a = state(c, "100", "0"), b = state(c, "100", "0", "gate", "ETH/USDT");
    grid::PortfolioCoordinator coordinator({}, {a.scope, b.scope}, {q("100"), q("100")});
    rejects([&] { (void)coordinator.evaluate({}, 0); }, "incomplete roster rejected",
            "complete increasing timestamp groups required");
    auto missing_state = row(a, c); missing_state.state = nullptr;
    rejects([&] { (void)coordinator.evaluate({missing_state, row(b, c)}, 0); },
            "null state rejected before dereference", "portfolio roster changed");
    auto missing_context = row(a, c); missing_context.context = nullptr;
    rejects([&] { (void)coordinator.evaluate({missing_context, row(b, c)}, 0); },
            "null context rejected before dereference", "portfolio roster changed");
    rejects([&] { (void)coordinator.evaluate({row(b, c), row(a, c)}, 0); },
            "scope at wrong roster index rejected", "portfolio roster changed");
    rejects([&] { (void)coordinator.evaluate({row(a, c), row(a, c)}, 0); },
            "duplicate scope cannot replace missing allocation", "portfolio roster changed");
    const auto valid = coordinator.evaluate({row(a, c), row(b, c)}, 0);
    check(valid.events.empty() && coordinator.cycles()[0].triggers == 0,
          "rejected rosters consume neither timestamp nor funded portfolio state");
}
void valuation() {
    auto c = context(); auto s = state(c, "100", "1.23456");
    c.sell_fee.rate = q(".001");
    auto nav = grid::allocation_nav(row(s, c, "10.12345"), {}, 0);
    check(nav.raw == q("112.498006432"), "raw mark uses exact close and all residual ownership");
    check(nav.net == q("112.47559"), "quote-fee liquidation forecast exact");
    check(nav.sellable == q("1.234") && nav.residual == q(".00056"), "amount rounding retains residual");
    c.sell_fee.max_settlements = 64;
    nav = grid::allocation_nav(row(s, c, "10.12345"), {}, 0);
    check(nav.net == q("112.47496"), "forecast includes settlement reserve");
    c.sell_fee = {"BASE", q(".001"), q(".001"), std::nullopt, 1}; s = state(c, "0", "1.001");
    nav = grid::allocation_nav(row(s, c, "10"), {}, 0);
    check(nav.net == q("10") && nav.residual == q("0") && nav.sellable == q("1"), "base fee covered in amount, never double charged");
    c.sell_fee = {"TOKEN", q(".001"), q(".00001"), q("2"), 1}; c.buy_fee = c.sell_fee;
    s = state(c, "100", "1"); s.fee_balances["TOKEN"] = q("1").exact_units(c.token_quantum);
    nav = grid::allocation_nav(row(s, c, "10"), {}, 0);
    check(nav.net == q("111.99") && nav.raw == q("112"), "owned token asset and future token fee separately valued");
    s = state(c, "100", ".0005");
    nav = grid::allocation_nav(row(s, c, "10"), {}, 0);
    check(nav.net == q("100") && nav.raw == q("100.005") && nav.residual == q(".0005"), "dust is owned with zero liquidation proceeds");
    auto missing = row(s, c); missing.current_close.reset(); missing.mark_ms.reset();
    nav = grid::allocation_nav(missing, {}, 0); check(!nav.net && !nav.raw, "missing owned mark unknown");
    s = state(c, "100", "0"); missing.state = &s;
    nav = grid::allocation_nav(missing, {}, 0); check(nav.net == q("100"), "cash-only scope needs no mark");
    s.reconcile_reason = "unknown fill";
    nav = grid::allocation_nav(missing, {}, 0); check(!nav.net, "uncertain ownership never authorizes TP");
    c = context(); c.market.sell_amount.maximum = q(".1"); s = state(c, "0", "10");
    nav = grid::allocation_nav(row(s, c, "100"), {}, 0);
    check(!nav.net && nav.raw == q("1000"), "liquidation order budget blocks excess executable quantity");
    c=context(); c.market.price_tick=c.market.prices.minimum=q("1e-13");
    c.market.sell_amount.maximum=q(".001"); c.market.sell_cost.minimum=q("0");
    s=state(c,"0",".003"); nav=grid::allocation_nav(row(s,c,".0000000006"),{},0);
    check(nav.sellable==q(".003") && nav.net==q(".000000000001"),"asset rounding occurs once after all chunks");
    c=context(); s=state(c); stamp(c,2000); c.rules_observed_ms=0;
    nav=grid::allocation_nav(row(s,c),{},2000); check(!nav.net,"stale market assumptions prevent NAV trigger");
}
void lifecycle() {
    auto c = context(); auto s = state(c);
    grid::PortfolioCoordinator coordinator({}, {s.scope}, {q("100")});
    auto step = coordinator.evaluate({row(s, c)}, 0); accept(s, step);
    check(coordinator.cycles()[0].phase == "exiting" && s.mode == "portfolio_exit", "global mandatory ten percent trigger");
    check(step.events[0].trigger_nav==q("110") && step.events[0].trigger_baseline==q("100") && step.events[0].cycle_number==1,
          "trigger observation records exact exit-adjusted NAV and immutable baseline");
    check(s.orders.size() == 1 && s.orders[0].role == "portfolio_exit", "exit uses common order admission");
    check(s.orders[0].price == q("11") && s.owned_base() == q("10").exact_units(c.base_quantum), "placing exit changes no financial ownership");
    rejects([&] { (void)coordinator.evaluate({row(s, c)}, 0); }, "same group cannot restart or reevaluate");
    stamp(c, 60000);
    auto filled = grid::reduce(s, grid::FillEvent{s.scope, "partial", s.orders[0].id, q("4"), q("11"), std::vector<grid::ActualFee>{}}, c);
    check(filled.accepted, "common reducer accepts partial exit fill"); s = std::move(filled.state);
    step = coordinator.evaluate({row(s, c)}, 60000); accept(s, step);
    check(s.owned_base() == q("6").exact_units(c.base_quantum) && coordinator.slots()[0].orders == 1, "partial exit preserves remaining ownership");
    stamp(c, 3600000, "9"); step = coordinator.evaluate({row(s, c, "9")}, c.now_ms); accept(s, step);
    check(s.orders[0].cancel_requested && coordinator.slots()[0].orders == 1, "TTL cancels without assuming finality");
    stamp(c, 3660000, "9");
    auto terminal = grid::reduce(s, grid::ReportEvent{s.scope, s.orders[0].id, "canceled", q("4"), true}, c);
    check(terminal.accepted, "authoritative partial cancellation reconciles"); s = std::move(terminal.state);
    step = coordinator.evaluate({row(s, c, "9")}, c.now_ms); accept(s, step);
    check(coordinator.slots()[0].orders == 2 && s.orders.back().price == q("10.45"), "reprice stays above immutable trigger floor");
    stamp(c, 3720000, "11");
    filled = grid::reduce(s, grid::FillEvent{s.scope, "rest", s.orders.back().id, q("6"), q("10.45"), std::vector<grid::ActualFee>{}}, c);
    check(filled.accepted, "common reducer accepts remaining actual fill"); s = std::move(filled.state);
    step = coordinator.evaluate({row(s, c)}, c.now_ms); accept(s, step);
    check(coordinator.cycles()[0].phase == "complete" && s.mode == "portfolio_exit", "completion stays held in same bar");
    check(s.quote_cash == q("106.7").exact_units(c.quote_quantum), "actual realized exit can miss triggering ten percent");
    check(step.events.back().completion_nav==q("106.7") && step.events.back().trigger_nav==q("110") && step.events.back().trigger_ms==0,
          "completed cycle records actual cash separately from its trigger NAV");
    stamp(c, 3780000); step = coordinator.evaluate({row(s, c)}, c.now_ms); accept(s, step);
    check(coordinator.cycles()[0].phase == "active" && coordinator.cycles()[0].baseline == q("106.7"), "later-bar cycle starts from actual cash");
    check(coordinator.cycles()[0].original_funding == q("100") && s.mode == "active", "original funding remains continuous");
    check(step.events[0].kind=="restart" && step.events[0].completion_nav==q("106.7") && step.events[0].baseline==q("100") &&
          !coordinator.cycles()[0].trigger_nav && coordinator.cycles()[0].cycles==2,
          "restart preserves completed cycle evidence before clearing current-cycle observations");
}
void limits_and_precedence() {
    auto c = context(); auto a = state(c), b = state(c, "0", "10", "gate", "ETH/USDT");
    grid::PortfolioPolicy policy; policy.exchange_enabled = policy.symbol_enabled = true;
    grid::PortfolioCoordinator together(policy, {a.scope, b.scope}, {q("100"), q("100")});
    auto step = together.evaluate({row(a,c), row(b,c)}, 0);
    check(together.cycles()[0].triggers == 1 && step.events.size() == 1, "global wins simultaneous three-tier triggers");
    check(together.owners()[0] == 0 && together.owners()[1] == 0 && step.actions.size() == 2, "single owner and one exit per allocation");
    b = state(c, "0", "10", "gate", "ETH/USDT");
    grid::PortfolioCoordinator scoped(policy, {a.scope, b.scope}, {q("100"), q("100")});
    step = scoped.evaluate({row(a,c), row(b,c,"9")}, 0);
    check(scoped.cycles()[0].phase == "active" && scoped.owners()[0] && !scoped.owners()[1], "independent winning exchange freezes only itself");
    check(scoped.cycles()[*scoped.owners()[0]].tier == "exchange", "exchange precedes symbol");
    policy.exchange_enabled = false;
    grid::PortfolioCoordinator symbol(policy, {a.scope,b.scope}, {q("100"),q("100")});
    step = symbol.evaluate({row(a,c),row(b,c,"9")},0);
    check(symbol.owners()[0] && symbol.cycles()[*symbol.owners()[0]].tier == "symbol", "symbol tier independent of exchange switch");
    auto dust = state(c,"110",".0005"); grid::PortfolioCoordinator blocked({}, {dust.scope}, {q("100")});
    step = blocked.evaluate({row(dust,c)},0); accept(dust,step);
    check(blocked.cycles()[0].phase == "blocked" && blocked.slots()[0].reason == "unsellable residual", "unapproved dust blocks completion");
    check(dust.owned_base() == q(".0005").exact_units(c.base_quantum), "dust never removed");
    auto exit = state(c); grid::PortfolioCoordinator deadline({}, {exit.scope}, {q("100")});
    step = deadline.evaluate({row(exit,c)},0); accept(exit,step);
    exit = grid::reduce(exit,grid::ReportEvent{exit.scope,exit.orders[0].id,"open",q("0"),false},c).state;
    stamp(c,14400000); step=deadline.evaluate({row(exit,c)},c.now_ms); accept(exit,step);
    check(exit.orders[0].cancel_requested, "total deadline cancels still unfilled order");
    stamp(c,14460000);
    exit = grid::reduce(exit,grid::ReportEvent{exit.scope,exit.orders[0].id,"canceled",q("0"),true},c).state;
    step=deadline.evaluate({row(exit,c)},c.now_ms); accept(exit,step);
    check(deadline.cycles()[0].phase=="blocked" && deadline.slots()[0].orders==1, "no replacement after total deadline");
    c = context(); auto low = state(c); c.sell_fee.rate=q(".001");
    grid::PortfolioCoordinator fee_threshold({}, {low.scope}, {q("100")});
    step=fee_threshold.evaluate({row(low,c)},0);
    check(fee_threshold.cycles()[0].phase=="active", "raw threshold alone cannot trigger");
}
void cancellation_barrier() {
    auto c=context(); auto a=state(c,"100","1"), b=state(c,"100","1","gate","ETH/USDT");
    a.anchor=q("10"); a.ladder_credits=3;
    a=grid::reduce(a,grid::ReconciledEvent{a.scope,1,1,std::nullopt,0},c).state;
    check(a.orders.size()==4,"ordinary basket SELL and three lower BUYs fixture");
    const auto initial=a.orders;
    for(const auto& order:initial)
        a=grid::reduce(a,grid::ReportEvent{a.scope,order.id,"open",q("0"),false},c).state;
    grid::PortfolioCoordinator coordinator({}, {a.scope,b.scope}, {q("100"),q("100")});
    auto step=coordinator.evaluate({row(a,c),row(b,c)},0); accept(a,step,0); accept(b,step,1);
    check(b.orders.empty(),"global barrier prevents exit in otherwise-ready allocation");
    for(const auto& order:a.orders) check(order.cancel_requested,"all ordinary orders cancel before portfolio exits");
    stamp(c,60000);
    a=grid::reduce(a,grid::CancelAckEvent{a.scope,initial[0].id},c).state;
    step=coordinator.evaluate({row(a,c),row(b,c)},c.now_ms); accept(a,step,0); accept(b,step,1);
    check(b.orders.empty(),"cancel acknowledgement is not terminal evidence");
    const auto late=initial[1];
    a=grid::reduce(a,grid::FillEvent{a.scope,"cancel-race",late.id,q(".1"),late.price,std::vector<grid::ActualFee>{}},c).state;
    check(a.owned_base()==q("1.1").exact_units(c.base_quantum),"late BUY is accounted while exit latched");
    for(const auto& order:initial)
        a=grid::reduce(a,grid::ReportEvent{a.scope,order.id,"canceled",order.id==late.id?q(".1"):q("0"),true},c).state;
    stamp(c,120000); step=coordinator.evaluate({row(a,c),row(b,c)},c.now_ms); accept(a,step,0); accept(b,step,1);
    check(a.orders.back().role=="portfolio_exit" && a.orders.back().amount==q("1.1").exact_units(c.base_quantum),"exit includes reconciled late fill inventory");
    check(b.orders.size()==1 && b.orders[0].role=="portfolio_exit","scope barrier releases both allocations together");
}
void reprice_budget_and_handoff() {
    auto c=context(); auto s=state(c);
    grid::PortfolioPolicy policy; policy.ttl_ms=60000; policy.duration_ms=10000000;
    grid::PortfolioCoordinator retries(policy,{s.scope},{q("100")});
    auto step=retries.evaluate({row(s,c)},0); accept(s,step);
    for(unsigned attempt=0;attempt<4;++attempt) {
        const auto id=s.orders.back().id;
        s=grid::reduce(s,grid::ReportEvent{s.scope,id,"open",q("0"),false},c).state;
        stamp(c,static_cast<std::int64_t>(attempt)*120000+60000);
        step=retries.evaluate({row(s,c)},c.now_ms); accept(s,step);
        check(s.orders.back().cancel_requested,"each expired exit canceled");
        s=grid::reduce(s,grid::ReportEvent{s.scope,id,"canceled",q("0"),true},c).state;
        stamp(c,c.now_ms+60000); step=retries.evaluate({row(s,c)},c.now_ms); accept(s,step);
    }
    check(retries.slots()[0].orders==4 && retries.cycles()[0].phase=="blocked","initial order plus exactly three reprices");
    c=context(); auto a=state(c),b=state(c,"0","10","gate","ETH/USDT");
    policy={}; policy.symbol_enabled=true;
    grid::PortfolioCoordinator handoff(policy,{a.scope,b.scope},{q("100"),q("100")});
    step=handoff.evaluate({row(a,c),row(b,c,"9")},0); accept(a,step,0); accept(b,step,1);
    check(handoff.cycles()[*handoff.owners()[0]].tier=="symbol","lower tier starts before later global threshold");
    a=grid::reduce(a,grid::ReportEvent{a.scope,a.orders[0].id,"open",q("0"),false},c).state;
    stamp(c,60000);
    step=handoff.evaluate({row(a,c),row(b,c,"11")},c.now_ms); accept(a,step,0); accept(b,step,1);
    check(handoff.owners()[0]==0 && handoff.owners()[1]==0,"global handoff owns each allocation once");
    check(a.orders.size()==1 && handoff.slots()[0].orders==1 && handoff.slots()[0].started_ms==0,"handoff retains existing LIMIT, budget and deadline");
    check(b.orders.size()==1,"global handoff places only missing scope exit");
    c=context(); a=state(c); b=state(c,"0","10","gate","ETH/USDT");
    grid::PortfolioCoordinator completed_handoff(policy,{a.scope,b.scope},{q("100"),q("100")});
    step=completed_handoff.evaluate({row(a,c),row(b,c,"9")},0); accept(a,step,0); accept(b,step,1);
    stamp(c,60000);
    a=grid::reduce(a,grid::FillEvent{a.scope,"symbol-done",a.orders[0].id,q("10"),q("11"),std::vector<grid::ActualFee>{}},c).state;
    step=completed_handoff.evaluate({row(a,c),row(b,c,"9")},c.now_ms); accept(a,step,0); accept(b,step,1);
    check(completed_handoff.cycles()[*completed_handoff.owners()[0]].phase=="complete","lower-tier exit completes before higher trigger");
    stamp(c,120000); step=completed_handoff.evaluate({row(a,c),row(b,c)},c.now_ms); accept(a,step,0); accept(b,step,1);
    check(completed_handoff.owners()[0]==0 && a.orders.size()==1 && a.orders[0].terminal(),"higher trigger preempts lower restart without transient BUY");
    policy={}; policy.max_cycles=1; c=context(); auto cash=state(c,"110","0");
    grid::PortfolioCoordinator maximum(policy,{cash.scope},{q("100")});
    step=maximum.evaluate({row(cash,c)},0); accept(cash,step);
    stamp(c,60000); step=maximum.evaluate({row(cash,c)},c.now_ms); accept(cash,step);
    check(maximum.cycles()[0].phase=="cycle_limit" && cash.mode=="portfolio_exit","cycle cap retains hold and blocks automatic restart");
    c=context(); auto missing_context=context();
    a=state(c,"110","0"); b=state(missing_context,"110","0","gate","ETH/USDT");
    grid::PortfolioCoordinator missing_restart({}, {a.scope,b.scope},{q("100"),q("100")});
    step=missing_restart.evaluate({row(a,c),row(b,missing_context)},0); accept(a,step,0); accept(b,step,1);
    stamp(c,60000);
    step=missing_restart.evaluate({row(a,c),{&b,&missing_context,std::nullopt,std::nullopt}},60000);
    accept(a,step,0); accept(b,step,1);
    check(a.mode=="active" && a.orders.size()==1 && b.mode=="active" && b.orders.empty(),
          "cash-only restart never creates new BUY from missing allocation's stale observation clock");
    check(missing_context.now_ms==0,"resume freshness guard does not fabricate a missing source observation");
}
void probe(int argc, char** argv) {
    if (argc != 15) throw grid::Error("nav-probe requires 13 arguments");
    auto c=context();
    c.market.price_tick=Rational(argv[5]); c.market.amount_tick=Rational(argv[6]);
    c.market.prices.minimum=c.market.price_tick;
    c.market.buy_amount.minimum=c.market.sell_amount.minimum=c.market.amount_tick;
    c.market.buy_cost.minimum=c.market.sell_cost.minimum=Rational(argv[7]);
    c.sell_fee={argv[8],Rational(argv[9]),Rational(argv[10]),std::nullopt,static_cast<unsigned>(std::stoul(argv[11]))};
    if(c.sell_fee.currency!="BASE" && c.sell_fee.currency!="QUOTE") c.sell_fee.quote_per_token=Rational(argv[12]);
    c.buy_fee=c.sell_fee;
    auto s=state(c,argv[3],argv[2]);
    if(c.sell_fee.currency!="BASE" && c.sell_fee.currency!="QUOTE") s.fee_balances[c.sell_fee.currency]=Rational(argv[13]).exact_units(c.token_quantum);
    grid::PortfolioPolicy policy; policy.nav_order_budget=static_cast<unsigned>(std::stoul(argv[14]));
    const auto nav=grid::allocation_nav(row(s,c,argv[4]),policy,0);
    const auto optional=[](const auto& value){ return value ? grid::json_quote(value->str()) : "null"; };
    std::cout<<"{\"raw\":"<<optional(nav.raw)<<",\"net\":"<<optional(nav.net)
      <<",\"sell_quantity\":"<<grid::json_quote(nav.sellable.str())<<",\"residual\":"<<grid::json_quote(nav.residual.str())
      <<",\"fee\":"<<grid::json_quote(nav.estimated_fee.str())<<",\"reason\":"<<grid::json_quote(nav.reason)<<"}\n";
}
} // namespace
int main(int argc,char** argv) {
    try {
        if(argc>1 && std::string(argv[1])=="--nav-probe") { probe(argc,argv); return 0; }
        if(argc!=1) throw grid::Error("unknown portfolio test command");
        public_roster_guards(); valuation(); lifecycle(); limits_and_precedence(); cancellation_barrier(); reprice_budget_and_handoff();
        std::cout<<checks<<" portfolio checks passed\n"; return 0;
    } catch(const std::exception& error) { std::cerr<<"portfolio check "<<checks<<": "<<error.what()<<'\n'; return 1; }
}
