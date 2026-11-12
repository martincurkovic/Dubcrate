import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import java.util.*;

// For each tag string, list functions that load a pointer to it (string <- pool word <- instruction).
public class TagUsers extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] tags = {"INDX", "INFO", "THUM", "DID ", "CN11", "HC11", "W001", "W002", "CSUM", ".PDJ", "MPJINDX", "CDJINDX", "PIONEER"};
        boolean decompile = getScriptArgs().length > 0 && getScriptArgs()[0].equals("decompile");
        Map<Function, Set<String>> users = new LinkedHashMap<>();
        for (String tag : tags) {
            byte[] pat = (tag + "\0").getBytes("ISO-8859-1");
            Address s = currentProgram.getMemory().findBytes(currentProgram.getMinAddress(), pat, null, true, monitor);
            if (s == null) { println(tag + ": string not found"); continue; }
            StringBuilder sb = new StringBuilder(tag + " @ " + s + ":");
            for (Reference r1 : getReferencesTo(s)) {
                Address pool = r1.getFromAddress();
                Function inPool = getFunctionContaining(pool);
                boolean any = false;
                for (Reference r2 : getReferencesTo(pool)) {
                    Function f = getFunctionContaining(r2.getFromAddress());
                    sb.append("  ").append(r2.getFromAddress()).append("->").append(f == null ? "?" : f.getEntryPoint().toString());
                    if (f != null) users.computeIfAbsent(f, k -> new TreeSet<>()).add(tag);
                    any = true;
                }
                if (!any) {
                    sb.append("  pool ").append(pool).append(inPool == null ? " (no fn)" : " in " + inPool.getEntryPoint());
                    if (inPool != null) users.computeIfAbsent(inPool, k -> new TreeSet<>()).add(tag);
                }
            }
            println(sb.toString());
        }
        println("\nFUNCTIONS");
        for (Map.Entry<Function, Set<String>> e : users.entrySet())
            println("  " + e.getKey().getEntryPoint() + " size " + e.getKey().getBody().getNumAddresses() + " tags " + e.getValue());
        if (decompile) {
            DecompInterface ifc = new DecompInterface();
            ifc.openProgram(currentProgram);
            for (Function f : users.keySet()) {
                DecompileResults res = ifc.decompileFunction(f, 90, monitor);
                println("\n===== " + f.getName() + " @ " + f.getEntryPoint() + " =====");
                println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "decompile failed");
            }
        }
    }
}
