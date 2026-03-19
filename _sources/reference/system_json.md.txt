## Viewing System Data in JSON Format

System data can be serialized to JSON files with `system.to_json("system.json")`.

It can be useful to view and filter the system data in this format. There
are many tools available to browse JSON data.

Here is an example [GUI tool](http://jsonviewer.stack.hu) that is available
online in a browser.

The command line utility [jq](https://stedolan.github.io/jq/) offers even more
features. The rest of this page provides example commands.

Some of the examples assume a UNIX operating system (our apologies to Windows users).

- View the entire file pretty-printed

```
$ jq . system.json
```

- View the System component types

```
$ jq -r '.components | .[] | .__metadata__.fields.type' system.json | sort | uniq
```

- View specific components

```
$ jq '.components | .[] | select(.__metadata__.fields.type == "Bus")' system.json
```

- Get the count of a component type

```
# There is almost certainly a better way.
$ jq '.components | .[] | select(.__metadata__.fields.type == "Bus")' system.json | grep -c Bus
```

- View specific component by name

```
$ jq '.components | .[] | select(.__metadata__.fields.type == "Bus" and .name == "bus1")' system.json
```

- Filter on a field value

```
$ jq '.components | .[] | select(.__metadata__.fields.type == "Bus" and .voltage > 1.1)' system.json
```
