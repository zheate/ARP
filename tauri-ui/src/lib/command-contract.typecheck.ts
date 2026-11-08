// Compile-only assertions. This function must never run in the product.
import type { CommandParams, CommandResult, RunCommand } from "./command-contract"
export function verifyCommandContract(run: RunCommand) {
  void run("powerSupply.setCurrent", { currentA: 2 })
  // @ts-expect-error current is measured in amperes, not an untyped string
  void run("powerSupply.setCurrent", { currentA: "2" })
  // @ts-expect-error misspelled command
  void run("powerSupply.setCurent", { currentA: 2 })
  // @ts-expect-error current-setting commands require a parameter object
  void run("powerSupply.setCurrent")
  // @ts-expect-error wrong parameter name
  const params: CommandParams<"powerSupply.setCurrent"> = { voltageV: 2 }
  // @ts-expect-error the shutdown response is not a data list
  const result: CommandResult<"app.shutdown"> = { records: [] }
  return { params, result }
}
