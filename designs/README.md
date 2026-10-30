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
last synchronised after ertlnagoya/cadl-robot-town#8, which marks the
deadlines of `DELIVERY_SLA` — its guarantees and its lifecycle — as
amendable in the contract layer),
the starting rules of Course D of the hands-on course
(<https://www.ertl.jp/cadl-spec/docs/handson/robot-town-tutorial>).
`robot_town.constitution.yaml` is a copy of that repository's
`constitution.yaml`: the layer (rule, contract), safety flag, range and step
of each `# @amend:` name. The **Versions** view lists the amendable
parameters, checks them against that constitution (or one you upload) and
amends a value in place, keeping the comments. The steward and the council
of the course write amended versions to `runs/<name>/v1.cadl`, `v2.cadl`,
...; add them all at once under **Versions → Add CADL files as versions**
(they are put in the order of their names) and compare any two there. The
history of the amendable values then also counts, per parameter, the
amendments, the steps moved in all, the largest move and the reversals —
the quantities of Exercise 12-1 of the course, which divides by the original
`step`, so use the constitution the run started from.
