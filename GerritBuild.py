import sys,subprocess
from os import environ as env

def cmake_istrue(s):
   return not (s.upper() in ("FALSE", "OFF", "NO") or s.upper().endswith("-NOTFOUND"))

def error(s):
   print(s)
   exit(1)

# if jenkins issue 12438 is resolved, options would be directly passed as args=env
# until then none of the OPTIONS key or values (including the host name)
# are allowed to contain space or = characters.
args = dict(map(lambda x: x.split("="), env["OPTIONS"].split(" ")))

#get all "GMX_" variables
opts = dict((k,v) for k,v in args.iteritems() if k.startswith("GMX_"))

env_cmd = "true"
build_cmd = "make -j2"
test_cmd = "ctest -DExperimentalTest -V"
call_opts = {}
opts_list = ""
generator = None
    
if "CMakeVersion" in args:
   env["PATH"] =  "%s/tools/cmake-%s/bin:%s" % (env["HOME"],args["CMakeVersion"],env["PATH"])

if not 'Compiler' in args or not 'CompilerVersion' in args or not 'host' in args:
   error("Compiler, CompilerVersion and host needs to be specified")

if args['Compiler']=="gcc":
   env["CC"]  = "gcc-"      + args["CompilerVersion"]
   env["CXX"] = "g++-"      + args["CompilerVersion"]
   env["FC"]  = "gfortran-" + args["CompilerVersion"] 

if args['Compiler']=="icc":
   if args["host"].lower().find("win")>-1:
      env_cmd = '"c:\\Program Files (x86)\\Microsoft Visual Studio 10.0\\VC\\vcvarsall.bat" amd64 && "c:\\Program Files (x86)\\Intel\\Composer XE\\bin\\compilervars.bat" intel64 vs2010shell'
      generator = 'Visual Studio 10 Win64'
      env["CC"]  = "icl"
      env["CXX"] = "icl"
   else:
      env_cmd = ". /opt/intel/bin/iccvars.sh intel64"
      env["CC"]  = "icc"
      env["CXX"] = "icpc"

if args['Compiler']=="msvc":
   if args['CompilerVersion']=='2008':
      env_cmd = '"c:\\Program Files (x86)\\Microsoft Visual Studio 9.0\\VC\\vcvarsall.bat" x86'
      generator = "Visual Studio 9 2008"
   elif args['CompilerVersion']=='2010':
      env_cmd = '"c:\\Program Files (x86)\\Microsoft Visual Studio 10.0\\VC\\vcvarsall.bat" amd64'
      generator = 'Visual Studio 10 Win64'
   else:
      error("MSVC only version 2008 and 2010 supported")

if generator != None:
   opts_list += '-G "%s" ' % (generator,)
   if generator=='Visual Studio 10 Win64':
      build_cmd = "msbuild /m:2 /p:Configuration=MinSizeRel All_Build.vcxproj"
   elif generator=="Visual Studio 9 2008":
      build_cmd = "devenv ALL_BUILD.vcproj /build MinSizeRel /project All_Build"      
   else:
      error("Generator %s not supported%(generator,)")

if "GMX_EXTERNAL" in opts.keys():
    v = opts.pop("GMX_EXTERNAL")
    opts["GMX_EXTERNAL_LAPACK"] = v
    opts["GMX_EXTERNAL_BLAS"] = v
    if cmake_istrue(v):
       if "Compiler" in args and args['Compiler']=="icc":
          opts_list += '-DGMX_FFT_LIBRARY=mkl  -DMKL_LIBRARIES="${MKLROOT}/lib/intel64/libmkl_intel_lp64.so;${MKLROOT}/lib/intel64/libmkl_sequential.so;${MKLROOT}/lib/intel64/libmkl_core.so" -DMKL_INCLUDE_DIR=${MKLROOT}/include '
       else:
          env["CMAKE_LIBRARY_PATH"] = "/usr/lib/atlas-base"

if "GMX_MPI" in opts.keys() and cmake_istrue(opts["GMX_MPI"]):
   if "CompilerVersion" in args:
      env["OMPI_CC"] =env["CC"]
      env["OMPI_CXX"]=env["CXX"]
      env["OMPI_FC"] =env["FC"]
   env["CC"] ="mpicc"
   env["CXX"]="mpic++"
   env["FC"] ="mpif90"

if not args["host"].lower().find("win")>-1:
   call_opts = {"executable":"/bin/bash"}

#construct string for all "GMX_" variables
opts_list += " ".join(["-D%s=%s"%(k,v) for k,v in opts.iteritems()])
opts_list += " -DGMX_DEFAULT_SUFFIX=off -DCMAKE_BUILD_TYPE=Debug ."

cmd = "%s && cmake --version && cmake %s && %s && %s" % (env_cmd,opts_list,build_cmd,test_cmd)

print "Running " + cmd

ret = subprocess.call(cmd, stdout=sys.stdout, stderr=sys.stderr, shell=True, **call_opts)
sys.exit(ret)


