import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import java.util.*;

// Args: string fragments (and "decompile"). Report functions that load a pointer to each.
public class StrUsers extends GhidraScript {
    @Override
    public void run() throws Exception {
        boolean dec = false;
        Set<Function> users = new LinkedHashSet<>();
        for (String a : getScriptArgs()) {
            if (a.equals("decompile")) { dec = true; continue; }
            Address s = currentProgram.getMemory().findBytes(currentProgram.getMinAddress(), a.getBytes("ISO-8859-1"), null, true, monitor);
            if (s == null) { println(a + ": not found"); continue; }
            StringBuilder sb = new StringBuilder(a + " @ " + s + ":");
            for (Reference r1 : getReferencesTo(s)) {
                Address pool = r1.getFromAddress();
                boolean any = false;
                for (Reference r2 : getReferencesTo(pool)) {
                    Function f = getFunctionContaining(r2.getFromAddress());
                    sb.append("  ").append(r2.getFromAddress()).append("->").append(f == null ? "?" : f.getEntryPoint().toString());
                    if (f != null) users.add(f);
                    any = true;
                }
                if (!any) sb.append("  pool ").append(pool).append(" (no loader)");
            }
            println(sb.toString());
        }
        if (dec) {
            DecompInterface ifc = new DecompInterface();
            ifc.openProgram(currentProgram);
            for (Function f : users) {
                DecompileResults res = ifc.decompileFunction(f, 90, monitor);
                println("\n===== " + f.getEntryPoint() + " size " + f.getBody().getNumAddresses() + " =====");
                println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "decompile failed");
            }
        }
    }
}
