import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

// Args: addresses. Print every reference Ghidra knows to each (calls, jumps, data), with the function it comes from.
public class RefsTo extends GhidraScript {
    @Override
    public void run() throws Exception {
        for (String a : getScriptArgs()) {
            Address at = toAddr(a);
            StringBuilder sb = new StringBuilder("refs to " + a + ":");
            for (Reference r : getReferencesTo(at)) {
                Function f = getFunctionContaining(r.getFromAddress());
                sb.append("  ").append(r.getFromAddress()).append("[").append(r.getReferenceType()).append("]->")
                  .append(f == null ? "?" : f.getEntryPoint().toString());
            }
            println(sb.toString());
        }
    }
}
