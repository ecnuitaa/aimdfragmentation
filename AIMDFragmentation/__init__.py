#!/usr/bin/env python3
import numpy as np
import sys, os, time
from GaussianRunner import GaussianRunner,GaussianAnalyst
from ase import Atoms
from ase.io import read as readxyz
from ase.geometry import get_distances
import subprocess as sp
from multiprocessing import Pool, cpu_count

class AIMDFragmentation(object):
    def __init__(self,nproc_sum=None,nproc=4,cutoff=3.5,xyzfilename="comb.xyz",pdbfilename="comb.pdb",qmmethod="mn15",qmbasis="6-31g(d)",addkw="",qmmem="400MW",atombondnumber={"C":4,"H":1,"O":2},logfile="force.log",outputfile="force.dat",unit=1,pbc=False,cell=[0,0,0],gaussian_dir="gaussian_files",command="g16",gaussiancommand=None,jobfile="gaussianjobs",onebodykeyword="scf=(xqc,MaxConventionalCycles=256)",twobodykeyword="guess=mix scf=(maxcyc=256)",kbodyfile="kbforce.dat"):
        self.nproc_sum=nproc_sum if nproc_sum else cpu_count()
        self.nproc=nproc
        self.cutoff=cutoff
        self.xyzfilename=xyzfilename
        self.pdbfilename=pdbfilename
        self.qmmethod=qmmethod
        self.qmbasis=qmbasis
        self.addkw=addkw
        self.qmmem=qmmem
        self.logfile=logfile
        self.outputfile=outputfile
        self.unit=unit
        self.pbc=pbc
        self.cell=cell
        self.atomid={}
        self.jobs=[]
        self.gaussian_dir=gaussian_dir
        self.errorfiles=[]
        self.command=command
        self.gaussiancommand=gaussiancommand
        self.jobfile=jobfile
        self.onebodykeyword=onebodykeyword
        self.twobodykeyword=twobodykeyword
        self.kbodyfile=kbodyfile
        self._fold=None

    def run(self):
        self.readbond()
        self.rungaussian()
        self.takeforce()

    def rungaussian(self):
        if not self.gaussiancommand:
            GaussianRunner(command=self.command,cpu_num=self.nproc_sum,nproc=self.nproc).runGaussianInParallel('gjf',[os.path.join(self.gaussian_dir,job+".gjf") for job in self.jobs])
        else:
            with open(self.jobfile,'w') as f:
                print(*[os.path.join(self.gaussian_dir,job+".gjf") for job in self.jobs],file=f)
            sp.call(self.gaussiancommand.split())

    def logging(self,*message):
        localtime = time.asctime( time.localtime(time.time()) )
        print(localtime,'AIMDFragmentation',*message)
        with open(self.logfile,'a') as f:
            print(localtime,'AIMDFragmentation',*message,file=f)

    def getjobname(self,*molid):
        if len(molid)==1:
            return "mol"+str(*molid)
        elif len(molid)==2:
            molid1,molid2=sorted(molid)
            return "tb"+str(molid1)+"-"+str(molid2)

    def mo(self,i,bond,molecule,done): #connect molecule
        molecule.append(i)
        done[i]=True
        for b in bond[i]:
            if not done[b]:
                molecule,done=self.mo(b,bond,molecule,done)
        return molecule,done

    def readpdb(self):
        bond=[[] for x in range(self.natom)]
        with open(self.pdbfilename) as f:
            for line in f:
                if line.startswith("CONECT"):
                    s=line.split()
                    bond[int(s[1])-1]+=[int(x)-1 for x in s[2:]]
        d,done=[],[False for x in range(self.natom)]
        for i in range(self.natom):
            if not done[i]:
                molecule,done=self.mo(i,bond,[],done)
                molecule.sort()
                d.append(molecule)
        self.mols=d

    def printgjf(self,jobname,selected_atoms1,S1,selected_atoms2=None,S2=None):
        if not os.path.exists(self.gaussian_dir):
            os.makedirs(self.gaussian_dir)
        with open(os.path.join(self.gaussian_dir,jobname+".gjf"),'w') as f:
            selected_atoms,Stotal,kbodykeyword=(selected_atoms1,S1,self.onebodykeyword) if not selected_atoms2 else (selected_atoms1+selected_atoms2,S1+S2-1,self.twobodykeyword)
            selected_atoms.wrap(center=selected_atoms[0].position/selected_atoms.get_cell_lengths_and_angles()[0:3],pbc=selected_atoms.get_pbc())
            print("%nproc="+str(self.nproc),file=f)
            print("%mem="+self.qmmem,file=f)
            print("#","force",self.qmmethod+"/"+self.qmbasis,kbodykeyword,self.addkw,"\n\n",jobname,"\n\n",0,Stotal,file=f)
            for atom in selected_atoms:
                print(atom.symbol,*atom.position,file=f)
            print("",file=f)

    def printmol(self):
        self.Smol=[]
        for molid,atoms in enumerate(self.mols,1):
            jobname=self.getjobname(molid)
            self.atomid[jobname]=atoms
            selected_atoms=self.atoms[atoms]
            # only supported for C,H,O
            num_H=sum(1 for atom in selected_atoms if atom.symbol=="H")
            S= 3 if selected_atoms.get_chemical_symbols()==['O','O'] else (1 + num_H % 2)
            self.Smol.append(S)
            self.printgjf(jobname,selected_atoms,S)
            self.jobs.append(jobname)

    def printtb(self):
        for molid1,atoms1 in enumerate(self.mols,1):
            for molid2,atoms2 in enumerate(self.mols[molid1:],molid1+1):
                if np.min(get_distances(self.atoms[atoms1].positions,self.atoms[atoms2].positions,cell=self.atoms.get_cell(),pbc=self.atoms.get_pbc())[1])<=self.cutoff:
                    jobname=self.getjobname(molid1,molid2)
                    self.atomid[jobname]=atoms1+atoms2
                    selected_atoms1,selected_atoms2=self.atoms[atoms1],self.atoms[atoms2]
                    S1,S2=self.Smol[molid1-1],self.Smol[molid2-1]
                    self.printgjf(jobname,selected_atoms1,S1,selected_atoms2,S2)
                    self.jobs.append(jobname)

    def readbond(self):
        self.atoms=readxyz(self.xyzfilename)
        self.atoms.set_pbc(self.pbc)
        self.atoms.set_cell(self.cell)
        self.natom=len(self.atoms)
        os.system('obabel -ixyz '+self.xyzfilename+' -opdb -O '+self.pdbfilename+' > /dev/null')
        self.readpdb()
        self.printmol()
        self.logging("Total S:",sum(self.Smol)-len(self.Smol)+1)
        self.printtb()

    def readforce(self,jobname):
        forces=GaussianAnalyst(properties=['force']).readFromLOG(os.path.join(self.gaussian_dir,jobname+'.log'))['force']
        if forces:
            atoms={}
            for index,force in forces.items():
                atoms[self.atomid[jobname][index-1]]=np.array(force)*self.unit
            return atoms,None
        else:
            return None,jobname

    @property
    def fold(self):
        if self._fold is None:
            if os.path.isfile(self.kbodyfile):
                loadingfold=np.loadtxt(self.kbodyfile)
                if loadingfold.shape==(self.natom,6):
                    self._fold=loadingfold
                    self.logging("Load old forces.")
        if self._fold is None:
            self._fold=np.zeros((self.natom,6))
            self.logging("No old forces found. Use 0 instead.")
        return self._fold

    def takeforce(self):
        onebodyforce,twobodyforce=np.zeros((self.natom,3)),np.zeros((self.natom,3))
        with Pool(self.nproc_sum) as pool:
            onebodyresults=pool.imap(self.readforce,[self.getjobname(i) for i in range(1,len(self.mols)+1)])
            twobodyresults=pool.imap(self.readforce,[self.getjobname(i,j) for i in range(1,len(self.mols)+1) for j in range(i+1,len(self.mols)+1) if self.getjobname(i,j) in self.jobs])
            twobodyerroratoms=[]
            for i,results in enumerate((onebodyresults,twobodyresults)):
                for atoms,jobname in results:
                    if atoms:
                        for atom,force in atoms.items():
                            if i==0:
                                onebodyforce[atom]=force
                            else:
                                twobodyforce[atom]+=force-onebodyforce[atom]
                    else:
                        self.logging('WARNING:','No forces of',jobname,'found. Use the old forces instead.')
                        self.errorfiles.append(os.path.join(self.gaussian_dir,jobname+'.log'))
                        if i==0:
                            onebodyforce[self.atomid[jobname]]=self.fold[self.atomid[jobname]][:,0:3]
                        else:
                            twobodyerroratoms+=self.atomid[jobname]
            if twobodyerroratoms:
                twobodyforce[twobodyerroratoms]=self.fold[twobodyerroratoms][:,3:6]
                self.logging("Atom",*twobodyerroratoms,"use(s) the old 2-body forces.")
        finalforces=onebodyforce+twobodyforce
        # Make the resultant force equal to 0
        if np.abs(np.sum(finalforces))>0:
            finalforces-=np.abs(finalforces)/np.sum(np.abs(finalforces),0)*np.sum(finalforces,0)
        np.savetxt(self.outputfile,finalforces,fmt='%16.9f')
        np.savetxt(self.kbodyfile,np.hstack((onebodyforce,twobodyforce)),fmt='%16.9f')
        forcesum=np.sum(finalforces,axis=0)
        self.logging("Resultant force:",*("%16.9f"%x for x in forcesum))
        forcesumdis=np.linalg.norm(forcesum)
        self.logging("Magnitude:","%16.9f"%forcesumdis)
