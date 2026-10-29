# RNA-seq FASTQ files

This is an example of a description that a user gives to the agent. Every name and number is invented.

The customer sent paired-end RNA-seq FASTQ files from 4 samples, as gzip files named like `S1_R1_001.fastq.gz` and `S1_R2_001.fastq.gz`. The reads are 100 bases long, with Illumina headers and a single 8-base index.

The analysis code runs an alignment and a count pipeline. A small file per sample is enough for the test data. A public transcriptome FASTA is not on this machine yet, so use a random genome for now.
