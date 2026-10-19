# Example designs

CADL designs offered under *Example design* on the Designer page.

They are copies of the examples in the upstream CADL repository
(<https://github.com/ertlnagoya/cadl/tree/master/examples>, Apache-2.0),
kept identical to them. `sos_dsl_robot_delivery.cadl` was last synchronised
with `cadl` master after ertlnagoya/cadl#26, which declares every message
of the example in the interfaces and adds the protocol `DeliveryOrder`; the
other examples are still identical to `cadl-lang` 0.3.14. Any `*.cadl` file
placed here appears in the list.

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
