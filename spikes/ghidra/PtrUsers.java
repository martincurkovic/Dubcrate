import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import java.util.*;

// Args: function entries (+ "decompile"). Find pool words equal to each entry, then the functions loading them.
public class PtrUsers extends GhidraScript {
    @Override
    public void run() throws Exception {
        boolean dec = false;
        Set<Function> users = new LinkedHashSet<>();
        for (String a : getScriptArgs()) {
            if (a.equals("decompile")) { dec = true; continue; }
            boolean raw = a.startsWith("raw:"); String aa = raw ? a.substring(4) : a; long v = raw ? toAddr(aa).getOffset() : (toAddr(aa).getOffset() + 0x20000000L) & 0xffffffffL;
            byte[] pat = new byte[]{(byte) (v >> 24), (byte) (v >> 16), (byte) (v >> 8), (byte) v};
            StringBuilder sb = new StringBuilder("users of " + a + ":");
            Address from = currentProgram.getMinAddress();
            while (true) {
                Address p = currentProgram.getMemory().findBytes(from, pat, null, true, monitor);
                if (p == null) break;
                for (Reference r : getReferencesTo(p)) {
                    Function f = getFunctionContaining(r.getFromAddress());
                    sb.append("  ").append(r.getFromAddress()).append("->").append(f == null ? "?" : f.getEntryPoint().toString());
                    if (f != null) users.add(f);
                }
                from = p.add(1);
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
