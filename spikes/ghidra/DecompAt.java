import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;

public class DecompAt extends GhidraScript {
    @Override
    public void run() throws Exception {
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        for (String a : getScriptArgs()) {
            Function f = getFunctionContaining(toAddr(a));
            if (f == null) { println("===== " + a + ": no function"); continue; }
            DecompileResults res = ifc.decompileFunction(f, 90, monitor);
            println("===== " + f.getEntryPoint() + " size " + f.getBody().getNumAddresses() + " =====");
            println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "decompile failed");
        }
    }
}
