# Example designs

CADL designs offered under *Example design* on the Designer page.

They are copies of the examples in the upstream CADL repository
(<https://github.com/ertlnagoya/cadl/tree/master/examples>, Apache-2.0),
kept identical to them. `sos_dsl_robot_delivery.cadl`,
`a_sos_robot_delivery.cadl` and `c_sos_taxi_fleet.cadl` were last
synchronised with `cadl` master after ertlnagoya/cadl#26 and #27, which
declare every message of the examples in their interfaces (#26 also adds the
protocol `DeliveryOrder`); `supply_chain.cadl` has not changed upstream
since `cadl-lang` 0.3.14. All four are identical to `cadl-lang` 0.3.18. Any `*.cadl` file
placed here appears in the list.

`supply_chain.cadl` is left as it is on purpose: it is the example of
findings on the **Control structure** view. MANUFACTURER controls WAREHOUSE
but gets no message back and has no monitor of it (an open loop), and
MANUFACTURER decides for RETAILER without sending it a control action. The
other designs here have no findings on that view; on the **Structure** view,
`robot_town.cadl` keeps the note "Nobody decides under DELIVERY_SLA.", since
that contract has no `decision_holder`.

`robot_town.cadl` is a copy of `cadl/robot_town.cadl` in the Robot Town
repository (<https://github.com/ertlnagoya/cadl-robot-town>, Apache-2.0;
last synchronised after ertlnagoya/cadl-robot-town#3, whose `AMEND` protocol
exchanges `violation_report` and `rule_update` between COUNCIL and
TOWN_STEWARD),
the starting rules of Course D of the hands-on course
(<https://www.ertl.jp/cadl-spec/docs/handson/robot-town-tutorial>). The
council of that course writes amended versions to `runs/<name>/v1.cadl`,
`v2.cadl`, ...; compare two of them on the Designer page: open one, save it
under **Versions**, open the next and compare the two there.
