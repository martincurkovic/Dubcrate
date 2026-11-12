import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;

// Args: start, end. Force-disassemble the range linearly and print it.
public class Dis extends GhidraScript {
    @Override
    public void run() throws Exception {
        Address s = toAddr(getScriptArgs()[0]);
        Address e = toAddr(getScriptArgs()[1]);
        clearListing(s, e);
        for (Address a = s; a.compareTo(e) < 0; ) {
            if (getInstructionAt(a) == null) disassemble(a);
            CodeUnit u = currentProgram.getListing().getCodeUnitAt(a);
            println(a + "  " + (u == null ? "--" : u.toString()));
            a = a.add(u == null ? 2 : u.getLength());
        }
    }
}
