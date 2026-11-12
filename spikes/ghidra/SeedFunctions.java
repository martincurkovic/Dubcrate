import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressRange;
import ghidra.program.model.mem.Memory;

// Seed functions at SuperH prologues: sts.l pr,@-r15 (0x4f22), so analysis can follow calls.
public class SeedFunctions extends GhidraScript {
    @Override
    public void run() throws Exception {
        Memory mem = currentProgram.getMemory();
        int seeded = 0;
        for (AddressRange range : mem.getLoadedAndInitializedAddressSet()) {
            Address a = range.getMinAddress();
            Address end = range.getMaxAddress().subtract(1);
            while (a.compareTo(end) < 0) {
                if ((a.getOffset() & 1) == 0 && (mem.getShort(a, true) & 0xffff) == 0x4f22) {
                    if (disassemble(a)) {
                        createFunction(a, null);
                        seeded++;
                    }
                }
                a = a.add(2);
            }
        }
        println("seeded " + seeded + " functions");
    }
}
