import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

// Args: addresses. Decompile the function at each (creating one first if there is none).
public class DecompAt extends GhidraScript {
    @Override
    public void run() throws Exception {
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        for (String a : getScriptArgs()) {
            Address at = toAddr(a);
            Function f = getFunctionContaining(at);
            if (f == null) {
                disassemble(at);
                f = createFunction(at, null);
            }
            if (f == null) { println("===== " + a + ": no function"); continue; }
            DecompileResults res = ifc.decompileFunction(f, 90, monitor);
            println("===== " + f.getEntryPoint() + " size " + f.getBody().getNumAddresses() + " =====");
            println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "decompile failed");
        }
    }
}
